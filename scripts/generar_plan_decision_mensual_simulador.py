# -*- coding: utf-8 -*-
"""Genera plan ejecutivo mensual cruzando finanzas, cocina y acciones.

Parte del diagnostico base del mes y no de capas ya ajustadas, para evitar que
la IA mezcle margen real simulado con margen posterior a recomendaciones.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan mensual de decision del simulador.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=3)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_plan_decision_mensual (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER,
            mes TEXT,
            estado_base TEXT,
            causa_principal TEXT,
            margen_base REAL DEFAULT 0,
            mejor_accion TEXT,
            margen_mejor_accion REAL DEFAULT 0,
            ahorro_cocina REAL DEFAULT 0,
            margen_combinado REAL DEFAULT 0,
            veredicto TEXT,
            decision TEXT,
            condicion_no_arruinarse TEXT,
            datos_json TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes)
        )
        """
    )


def money(v: float) -> float:
    return round(float(v or 0), 2)


def mejor_accion(conn: sqlite3.Connection, corrida_id: int, centro_id: int, mes: str) -> sqlite3.Row | None:
    if not table_exists(conn, "simulador_finanzas_acciones_mensuales"):
        return None
    return conn.execute(
        """
        SELECT *
        FROM simulador_finanzas_acciones_mensuales
        WHERE corrida_id=? AND centro_id=? AND mes=?
        ORDER BY margen_estimado DESC, mejora_margen DESC
        LIMIT 1
        """,
        (corrida_id, centro_id, mes),
    ).fetchone()


def ahorro_cocina(conn: sqlite3.Connection, corrida_id: int, centro_id: int, mes: str) -> sqlite3.Row | None:
    if not table_exists(conn, "simulador_impacto_cocina_mensual"):
        return None
    return conn.execute(
        """
        SELECT *
        FROM simulador_impacto_cocina_mensual
        WHERE corrida_id=? AND centro_id=? AND mes=?
        """,
        (corrida_id, centro_id, mes),
    ).fetchone()


def veredicto(margen_base: float, margen_accion: float, margen_combinado: float) -> tuple[str, str]:
    if margen_base >= 0:
        return "mantener_control", "El mes es rentable; aplicar cocina/controles solo para proteger margen."
    if margen_combinado >= 0:
        return "rescatable", "El mes se puede rescatar combinando accion comercial/operativa y cocina con control semanal."
    if margen_accion > margen_base or margen_combinado > margen_base:
        return "mejora_no_basta", "Las palancas mejoran, pero no bastan: revisar apertura, plantilla, canal, cupos o estrategia de temporada."
    return "no_rescatable", "No ejecutar campanas a ciegas: primero redimensionar coste base o cambiar modelo de venta."


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        if not table_exists(conn, "simulador_finanzas_diagnostico_mensual"):
            raise SystemExit("Falta simulador_finanzas_diagnostico_mensual.")
        rows = conn.execute(
            """
            SELECT *
            FROM simulador_finanzas_diagnostico_mensual
            WHERE corrida_id=? AND centro_id=?
            ORDER BY mes
            """,
            (args.corrida_id, args.centro_id),
        ).fetchall()
        count = 0
        for row in rows:
            accion = mejor_accion(conn, args.corrida_id, args.centro_id, row["mes"])
            cocina = ahorro_cocina(conn, args.corrida_id, args.centro_id, row["mes"])
            margen_base = float(row["margen"] or 0)
            margen_accion = float(accion["margen_estimado"] if accion else margen_base)
            cocina_ahorro = float(cocina["ahorro_estimado"] if cocina else 0)
            margen_combinado = margen_accion + cocina_ahorro
            ver, decision = veredicto(margen_base, margen_accion, margen_combinado)
            condicion = row["condicion_no_arruinarse"] or "No aceptar ventas sin margen neto minimo y control operativo."
            if accion and accion["condicion_control"]:
                condicion = f"{condicion} Control adicional: {accion['condicion_control']}"
            datos = {
                "diagnostico": dict(row),
                "mejor_accion": dict(accion) if accion else None,
                "cocina": dict(cocina) if cocina else None,
            }
            conn.execute(
                """
                INSERT INTO simulador_plan_decision_mensual
                (corrida_id, centro_id, mes, estado_base, causa_principal, margen_base,
                 mejor_accion, margen_mejor_accion, ahorro_cocina, margen_combinado,
                 veredicto, decision, condicion_no_arruinarse, datos_json, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(corrida_id, centro_id, mes) DO UPDATE SET
                    estado_base=excluded.estado_base,
                    causa_principal=excluded.causa_principal,
                    margen_base=excluded.margen_base,
                    mejor_accion=excluded.mejor_accion,
                    margen_mejor_accion=excluded.margen_mejor_accion,
                    ahorro_cocina=excluded.ahorro_cocina,
                    margen_combinado=excluded.margen_combinado,
                    veredicto=excluded.veredicto,
                    decision=excluded.decision,
                    condicion_no_arruinarse=excluded.condicion_no_arruinarse,
                    datos_json=excluded.datos_json
                """
                ,
                (
                    args.corrida_id,
                    args.centro_id,
                    row["mes"],
                    row["estado"],
                    row["causa_principal"],
                    money(margen_base),
                    accion["accion"] if accion else None,
                    money(margen_accion),
                    money(cocina_ahorro),
                    money(margen_combinado),
                    ver,
                    decision,
                    condicion,
                    json.dumps(datos, ensure_ascii=False),
                    EMPRESA_ID,
                ),
            )
            count += 1
        conn.commit()
        resumen = conn.execute(
            """
            SELECT veredicto, COUNT(*) total
            FROM simulador_plan_decision_mensual
            WHERE corrida_id=? AND centro_id=?
            GROUP BY veredicto
            ORDER BY veredicto
            """,
            (args.corrida_id, args.centro_id),
        ).fetchall()
        print({"planes": count, "resumen": [dict(r) for r in resumen]})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

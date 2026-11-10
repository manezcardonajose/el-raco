# -*- coding: utf-8 -*-
"""Convierte roturas PRL/APPCC de la auditoria en acciones PRL operativas."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crear acciones PRL desde auditoria de calidad.")
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
        CREATE TABLE IF NOT EXISTS prl_acciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            riesgo_id INTEGER,
            titulo TEXT NOT NULL,
            descripcion TEXT,
            prioridad TEXT DEFAULT 'media',
            estado TEXT DEFAULT 'pendiente',
            responsable TEXT,
            fecha_limite TEXT,
            evidencia TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )


def riesgo_id(conn: sqlite3.Connection, codigo: str, empresa_id: int, centro_id: int) -> int | None:
    if not table_exists(conn, "prl_riesgos"):
        return None
    row = conn.execute(
        "SELECT id FROM prl_riesgos WHERE codigo=? AND empresa_id=? AND centro_id=? LIMIT 1",
        (codigo, empresa_id, centro_id),
    ).fetchone()
    return int(row["id"]) if row else None


def accion_para_issue(issue: sqlite3.Row, empresa_id: int, centro_id: int, conn: sqlite3.Connection) -> dict | None:
    codigo = issue["codigo"]
    prioridad = "critica" if issue["severidad"] == "critica" else "alta" if issue["severidad"] == "alta" else "media"
    if codigo == "manipuladores_sin_carnet":
        return {
            "riesgo_id": riesgo_id(conn, "PRL-ALI-001", empresa_id, centro_id),
            "titulo": "Regularizar carnets de manipulador antes de operar alimentos",
            "descripcion": issue["detalle"] + " Revisar fichas, bloquear manipulacion sin carnet vigente y planificar renovacion/formacion.",
            "prioridad": prioridad,
            "responsable": "RRHH / Responsable APPCC",
            "dias": 3,
        }
    if codigo == "prl_pendiente_empleados_operativos":
        return {
            "riesgo_id": None,
            "titulo": "Cerrar PRL pendiente en empleados operativos",
            "descripcion": issue["detalle"] + " Programar formacion, recoger evidencia y actualizar ficha de empleado.",
            "prioridad": prioridad,
            "responsable": "RRHH / Direccion",
            "dias": 7,
        }
    if codigo == "formacion_prl_masiva_pendiente":
        return {
            "riesgo_id": None,
            "titulo": "Plan masivo de formacion PRL asignada",
            "descripcion": issue["detalle"] + " Convertir asignaciones pendientes en sesiones, cuestionario y firma.",
            "prioridad": prioridad,
            "responsable": "RRHH",
            "dias": 10,
        }
    return None


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
            raise SystemExit("No hay corrida.")
        if not table_exists(conn, "simulador_calidad_datos"):
            raise SystemExit("No hay auditoria de calidad.")
        issues = conn.execute(
            """
            SELECT *
            FROM simulador_calidad_datos
            WHERE corrida_id=? AND empresa_id=? AND centro_id=?
              AND area IN ('prl','prl_appcc')
              AND severidad IN ('critica','alta')
            ORDER BY CASE severidad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END
            """,
            (corrida_id, args.empresa_id, args.centro_id),
        ).fetchall()
        creadas = 0
        for issue in issues:
            accion = accion_para_issue(issue, args.empresa_id, args.centro_id, conn)
            if not accion:
                continue
            evidencia = f"simulador_calidad:{corrida_id}:{issue['codigo']}"
            exists = conn.execute(
                """
                SELECT 1 FROM prl_acciones
                WHERE empresa_id=? AND centro_id=? AND evidencia=? AND estado!='cerrada'
                LIMIT 1
                """,
                (args.empresa_id, args.centro_id, evidencia),
            ).fetchone()
            if exists:
                continue
            limite = date.today() + timedelta(days=int(accion["dias"]))
            conn.execute(
                """
                INSERT INTO prl_acciones
                (riesgo_id, titulo, descripcion, prioridad, estado, responsable,
                 fecha_limite, evidencia, empresa_id, centro_id)
                VALUES (?, ?, ?, ?, 'pendiente', ?, ?, ?, ?, ?)
                """,
                (
                    accion["riesgo_id"],
                    accion["titulo"],
                    accion["descripcion"],
                    accion["prioridad"],
                    accion["responsable"],
                    limite.isoformat(),
                    evidencia,
                    args.empresa_id,
                    args.centro_id,
                ),
            )
            creadas += 1
        conn.commit()
        print({"acciones_creadas": creadas, "issues_prl": len(issues), "corrida_id": corrida_id})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Convierte roturas APPCC/alimentarias de la auditoria en acciones operativas."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crear acciones de seguridad alimentaria desde auditoria.")
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
        CREATE TABLE IF NOT EXISTS seguridad_alimentaria_acciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            descripcion TEXT,
            area TEXT DEFAULT 'APPCC',
            prioridad TEXT DEFAULT 'media',
            estado TEXT DEFAULT 'pendiente',
            responsable TEXT,
            fecha_limite TEXT,
            origen TEXT,
            evidencia TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )


def accion_para_issue(issue: sqlite3.Row) -> dict | None:
    codigo = issue["codigo"]
    prioridad = "critica" if issue["severidad"] == "critica" else "alta" if issue["severidad"] == "alta" else "media"
    if codigo == "manipuladores_sin_carnet":
        return {
            "titulo": "Bloquear manipulacion alimentaria sin carnet vigente",
            "descripcion": issue["detalle"] + " Validar carnets, renovar caducados y no asignar tareas de cocina/sala/buffet sin formacion vigente.",
            "area": "Manipulador de alimentos",
            "prioridad": prioridad,
            "responsable": "RRHH / Responsable APPCC / Jefe de departamento",
            "dias": 2,
        }
    if codigo == "produccion_sin_appcc":
        return {
            "titulo": "Cerrar controles APPCC en produccion diaria",
            "descripcion": issue["detalle"] + " Revisar partes de produccion, temperaturas, conservacion, regeneracion, sobrantes y responsable por linea.",
            "area": "Produccion APPCC",
            "prioridad": prioridad,
            "responsable": "Jefe de cocina / Responsable APPCC",
            "dias": 1,
        }
    if codigo == "produccion_sin_ingredientes":
        return {
            "titulo": "Vincular produccion con ingredientes y lotes reales",
            "descripcion": issue["detalle"] + " No validar produccion sin ingredientes, lote, salida de almacen y trazabilidad minima.",
            "area": "Trazabilidad cocina",
            "prioridad": prioridad,
            "responsable": "Jefe de cocina / Economato",
            "dias": 3,
        }
    if codigo in {"sin_controles_appcc", "appcc_incompleto"}:
        return {
            "titulo": "Completar cobertura de controles APPCC",
            "descripcion": issue["detalle"] + " Crear o cerrar los controles obligatorios antes de dar por valido el servicio.",
            "area": "Plan APPCC",
            "prioridad": prioridad,
            "responsable": "Responsable APPCC",
            "dias": 2,
        }
    if codigo == "buffet_clasificacion_mala":
        return {
            "titulo": "Revisar clasificacion alimentaria del montaje buffet",
            "descripcion": issue["detalle"] + " Reubicar bolleria, quesos, fiambres, calientes y frios segun temperatura, alergeno y flujo de servicio.",
            "area": "Buffet APPCC",
            "prioridad": prioridad,
            "responsable": "Maitre / Jefe de cocina / Responsable APPCC",
            "dias": 2,
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
              AND (
                area IN ('appcc','prl_appcc','produccion','buffet')
                OR codigo IN ('sin_controles_appcc','appcc_incompleto')
              )
              AND severidad IN ('critica','alta','media')
            ORDER BY CASE severidad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END
            """,
            (corrida_id, args.empresa_id, args.centro_id),
        ).fetchall()
        creadas = 0
        for issue in issues:
            accion = accion_para_issue(issue)
            if not accion:
                continue
            evidencia = f"simulador_calidad:{corrida_id}:{issue['codigo']}"
            exists = conn.execute(
                """
                SELECT 1 FROM seguridad_alimentaria_acciones
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
                INSERT INTO seguridad_alimentaria_acciones
                (titulo, descripcion, area, prioridad, estado, responsable,
                 fecha_limite, origen, evidencia, empresa_id, centro_id)
                VALUES (?, ?, ?, ?, 'pendiente', ?, ?, 'auditoria_simulador', ?, ?, ?)
                """,
                (
                    accion["titulo"],
                    accion["descripcion"],
                    accion["area"],
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
        print({"acciones_creadas": creadas, "issues_alimentarios": len(issues), "corrida_id": corrida_id})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

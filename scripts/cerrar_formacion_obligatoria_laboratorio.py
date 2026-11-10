# -*- coding: utf-8 -*-
"""Cierra formacion obligatoria PRL/manipulador en laboratorio.

Simula que RRHH ejecuta el plan correctivo: marca PRL realizada, renueva carnet
de manipulador a perfiles de cocina/sala y cierra asignaciones pendientes con
evidencia. Sirve para comprobar si la auditoria baja riesgos al corregirlos.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cerrar formacion obligatoria de laboratorio.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=3)
    parser.add_argument("--fecha", default="2026-06-06")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def area_text(row: sqlite3.Row) -> str:
    return " ".join(str(row[k] or "") for k in row.keys() if k in {"rol", "departamento", "subdepartamento"}).lower()


def manipula_alimentos(texto: str) -> bool:
    return any(p in texto for p in ["cocina", "bar", "bares", "restaurante", "sala", "buffet", "plonge", "chef", "camarer"])


def area_operativa(texto: str) -> bool:
    return manipula_alimentos(texto) or any(p in texto for p in ["mantenimiento", "tecnico", "piso", "gobernanta", "limpieza", "recepcion", "reservas", "administracion", "animacion", "animador", "eventos"])


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    fecha = date.fromisoformat(args.fecha)
    caducidad = date(fecha.year + 2, fecha.month, fecha.day).isoformat()
    conn = connect(db_path)
    try:
        if not table_exists(conn, "empleados"):
            raise SystemExit("No existe empleados.")
        empleados = conn.execute(
            """
            SELECT e.id, e.rol, d.nombre AS departamento, sd.nombre AS subdepartamento
            FROM empleados e
            LEFT JOIN departamentos d ON d.id=e.departamento_id
            LEFT JOIN subdepartamentos sd ON sd.id=e.subdepartamento_id
            WHERE e.empresa_id=? AND e.centro_id=? AND COALESCE(e.activo,1)=1
            """,
            (args.empresa_id, args.centro_id),
        ).fetchall()
        prl = carnet = 0
        for emp in empleados:
            texto = area_text(emp)
            if area_operativa(texto):
                conn.execute(
                    """
                    UPDATE empleados
                    SET prl_realizada=1, fecha_prl=?, updated_at=CURRENT_TIMESTAMP,
                        motivo_cambio='Simulacion: cierre formacion PRL obligatoria'
                    WHERE id=? AND empresa_id=? AND centro_id=?
                    """,
                    (args.fecha, emp["id"], args.empresa_id, args.centro_id),
                )
                prl += 1
            if manipula_alimentos(texto):
                conn.execute(
                    """
                    UPDATE empleados
                    SET carnet_manipulador=1, fecha_caducidad_carnet=?,
                        updated_at=CURRENT_TIMESTAMP,
                        motivo_cambio='Simulacion: renovacion manipulador alimentos'
                    WHERE id=? AND empresa_id=? AND centro_id=?
                    """,
                    (caducidad, emp["id"], args.empresa_id, args.centro_id),
                )
                carnet += 1
        formaciones = 0
        if table_exists(conn, "prl_formacion_empleados"):
            cur = conn.execute(
                """
                UPDATE prl_formacion_empleados
                SET estado='realizada',
                    fecha_realizacion=?,
                    nota=8.5,
                    evidencia='simulacion_cierre_formacion_obligatoria'
                WHERE empresa_id=? AND centro_id=? AND estado<>'realizada'
                """,
                (args.fecha, args.empresa_id, args.centro_id),
            )
            formaciones = cur.rowcount
        conn.commit()
        print({"prl_actualizados": prl, "carnets_actualizados": carnet, "formaciones_cerradas": formaciones, "caducidad_carnet": caducidad})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

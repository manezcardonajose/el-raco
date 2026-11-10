# -*- coding: utf-8 -*-
"""Ajusta Hotel Albarracin a su estructura operativa real inicial."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
EMPRESA_ID = 2
CENTRO_ID = 8

DEPARTAMENTOS = [
    ("ADMINISTRACION", "general", 0, 0, 0),
    ("RECEPCION", "recepcion", 0, 0, 0),
    ("SALA / RESTAURANTE", "restaurante", 1, 1, 0),
    ("PISOS", "pisos", 1, 1, 0),
    ("COCINA", "cocina", 1, 1, 0),
    ("MANTENIMIENTO", "mantenimiento", 1, 1, 1),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ajustar estructura Hotel Albarracin.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def dep_id(conn: sqlite3.Connection, empresa_id: int, centro_id: int, nombre: str) -> int | None:
    row = conn.execute(
        """
        SELECT id FROM departamentos
        WHERE empresa_id=? AND centro_id=? AND UPPER(nombre)=UPPER(?)
        """,
        (empresa_id, centro_id, nombre),
    ).fetchone()
    return int(row["id"]) if row else None


def ensure_dep(conn: sqlite3.Connection, empresa_id: int, centro_id: int, nombre: str, tipo: str, appcc: int, stock: int, mantenimiento: int) -> int:
    found = dep_id(conn, empresa_id, centro_id, nombre)
    if found:
        conn.execute(
            """
            UPDATE departamentos
            SET activo=1, tipo_operativo=?, gestiona_appcc=?, gestiona_stock=?,
                gestiona_mantenimiento=?, permite_tpv=?
            WHERE id=?
            """,
            (tipo, appcc, stock, mantenimiento, 1 if tipo == "restaurante" else 0, found),
        )
        return found
    cur = conn.execute(
        """
        INSERT INTO departamentos
        (nombre, descripcion, activo, empresa_id, centro_id, tipo_operativo,
         gestiona_appcc, gestiona_stock, gestiona_mantenimiento, permite_tpv)
        VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            nombre,
            f"Departamento {nombre} - Hotel Albarracin",
            empresa_id,
            centro_id,
            tipo,
            appcc,
            stock,
            mantenimiento,
            1 if tipo == "restaurante" else 0,
        ),
    )
    return int(cur.lastrowid)


def ensure_pv(conn: sqlite3.Connection, empresa_id: int, centro_id: int, dep_sala: int, nombre: str, tipo: str) -> int:
    row = conn.execute(
        """
        SELECT id FROM puntos_venta
        WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER(?)
        """,
        (empresa_id, centro_id, nombre),
    ).fetchone()
    if row:
        pv_id = int(row["id"])
        conn.execute(
            """
            UPDATE puntos_venta
            SET activo=1, tipo=?, departamento_id=?, subdepartamento_id=NULL,
                tpv_tipo=?, tpv_admite_cargo_habitacion=1
            WHERE id=?
            """,
            (tipo, dep_sala, "bar" if tipo == "cafeteria" else "restaurante", pv_id),
        )
        return pv_id
    cur = conn.execute(
        """
        INSERT INTO puntos_venta
        (nombre, tipo, activo, departamento_id, subdepartamento_id, imprimir_comanda,
         empresa_id, tpv_tipo, tpv_control_buffet, tpv_admite_cargo_habitacion,
         tpv_permite_todo_incluido, tpv_bebidas_incluidas, centro_id)
        VALUES (?, ?, 1, ?, NULL, 1, ?, ?, 0, 1, 0, 0, ?)
        """,
        (nombre, tipo, dep_sala, empresa_id, "bar" if tipo == "cafeteria" else "restaurante", centro_id),
    )
    return int(cur.lastrowid)


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        empresa_id = args.empresa_id
        centro_id = args.centro_id

        old_direccion = dep_id(conn, empresa_id, centro_id, "DIRECCION")
        if old_direccion:
            conn.execute("UPDATE departamentos SET nombre='ADMINISTRACION', tipo_operativo='general' WHERE id=?", (old_direccion,))

        old_rest = dep_id(conn, empresa_id, centro_id, "RESTAURANTE / BAR")
        if old_rest:
            conn.execute(
                """
                UPDATE departamentos
                SET nombre='SALA / RESTAURANTE', tipo_operativo='restaurante',
                    gestiona_appcc=1, gestiona_stock=1, permite_tpv=1
                WHERE id=?
                """,
                (old_rest,),
            )

        activos = {}
        for nombre, tipo, appcc, stock, mantenimiento in DEPARTAMENTOS:
            activos[nombre] = ensure_dep(conn, empresa_id, centro_id, nombre, tipo, appcc, stock, mantenimiento)

        conn.execute(
            f"""
            UPDATE departamentos
            SET activo=0
            WHERE empresa_id=? AND centro_id=? AND id NOT IN ({','.join('?' for _ in activos.values())})
            """,
            (empresa_id, centro_id, *activos.values()),
        )

        conn.execute(
            """
            UPDATE subdepartamentos
            SET activo=0
            WHERE empresa_id=? AND centro_id=?
            """,
            (empresa_id, centro_id),
        )
        conn.execute(
            """
            UPDATE empleados
            SET subdepartamento_id=NULL
            WHERE empresa_id=? AND centro_id=?
            """,
            (empresa_id, centro_id),
        )
        conn.execute(
            """
            UPDATE empleados
            SET departamento_id=?
            WHERE empresa_id=? AND centro_id=? AND departamento_id IN (
                SELECT id FROM departamentos
                WHERE empresa_id=? AND centro_id=? AND UPPER(nombre) IN ('COMPRAS / ALMACEN','RRHH')
            )
            """,
            (activos["ADMINISTRACION"], empresa_id, centro_id, empresa_id, centro_id),
        )

        dep_sala = activos["SALA / RESTAURANTE"]
        cafeteria_id = ensure_pv(conn, empresa_id, centro_id, dep_sala, "Cafetería", "cafeteria")
        restaurante_id = ensure_pv(conn, empresa_id, centro_id, dep_sala, "Restaurante", "restaurante")

        conn.commit()
        resumen = {
            "empresa_id": empresa_id,
            "centro_id": centro_id,
            "departamentos_activos": len(activos),
            "subdepartamentos_activos": conn.execute(
                "SELECT COUNT(*) FROM subdepartamentos WHERE empresa_id=? AND centro_id=? AND COALESCE(activo,1)=1",
                (empresa_id, centro_id),
            ).fetchone()[0],
            "puntos_venta": {"cafeteria": cafeteria_id, "restaurante": restaurante_id},
        }
        print(resumen)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

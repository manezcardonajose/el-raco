# -*- coding: utf-8 -*-
"""Normaliza nombres de la plantilla simulada de Hotel 1.

Solo toca empleados con origen_importacion='plantilla_operativa_hotel1'.
Sirve para corregir plantillas antiguas creadas con nombres reciclados.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"

NOMBRES_FIJOS = [
    ("Juan", "Sanchez Camacho"),
    ("Fisa", "Sancho Jimenez"),
    ("Ana", "Martinez Roca"),
    ("Pedro", "Garcia Alonso"),
    ("Maria", "Torres Vidal"),
    ("Carlos", "Navarro Soler"),
    ("Laura", "Perez Medina"),
    ("Miguel", "Romero Gil"),
    ("Sara", "Cabrera Leon"),
    ("David", "Molina Cruz"),
    ("Nuria", "Santos Ortega"),
    ("Ruben", "Vega Martin"),
    ("Elena", "Castro Mora"),
    ("Javier", "Lopez Pons"),
    ("Marta", "Hernandez Rius"),
    ("Sergio", "Dominguez Serra"),
]

NOMBRES = [
    "Cristina", "Raul", "Paula", "Toni", "Isabel", "Pablo", "Noelia", "Adrian",
    "Silvia", "Oscar", "Beatriz", "Hector", "Rocio", "Daniel", "Alicia", "Marcos",
    "Carmen", "Victor", "Lorena", "Alba", "Ivan", "Teresa", "Clara", "Manuel",
    "Patricia", "Joel", "Irene", "Alejandro", "Eva", "Samuel", "Monica", "Jordi",
    "Natalia", "Xavi", "Ines", "Gabriel", "Sandra", "Mario", "Lidia", "Bruno",
]

APELLIDOS = [
    "Fuentes", "Aguilar", "Morales", "Ramos", "Benitez", "Marin", "Suarez", "Prieto",
    "Serrano", "Costa", "Flores", "Reyes", "Mendez", "Pascual", "Soria", "Vargas",
    "Campos", "Blanco", "Galvez", "Iglesias", "Nadal", "Ferrer", "Vila", "Soto",
    "Riera", "Pastor", "Crespo", "Calvo", "Paredes", "Luna", "Carrasco", "Bueno",
    "Nieto", "Vives", "Soler", "Cano", "Cortes", "Duran", "Casas", "Mora",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Renombra empleados simulados Hotel 1 con nombres unicos.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Base SQLite a corregir.")
    parser.add_argument("--centro-id", type=int, default=3)
    return parser.parse_args()


def nombre_unico(indice: int) -> tuple[str, str]:
    if indice < len(NOMBRES_FIJOS):
        return NOMBRES_FIJOS[indice]
    local = indice - len(NOMBRES_FIJOS)
    nombre = NOMBRES[local % len(NOMBRES)]
    a1 = APELLIDOS[(local * 2) % len(APELLIDOS)]
    a2 = APELLIDOS[(local * 2 + 11) % len(APELLIDOS)]
    vuelta = local // len(NOMBRES)
    if vuelta:
        a2 = f"{a2} {vuelta + 2}"
    return nombre, f"{a1} {a2}"


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, rol, departamento_id, subdepartamento_id, codigo_importacion
            FROM empleados
            WHERE empresa_id = 1
              AND centro_id = ?
              AND origen_importacion = 'plantilla_operativa_hotel1'
              AND COALESCE(activo, 1) = 1
            ORDER BY departamento_id, subdepartamento_id, rol, codigo_importacion, id
            """,
            (args.centro_id,),
        ).fetchall()
        for idx, row in enumerate(rows):
            nombre, apellidos = nombre_unico(idx)
            email = f"h1.{idx + 1:03d}.{nombre.lower()}@simulacion.local"
            telefono = f"+34 690 {idx // 100:03d} {idx % 100:03d}"
            conn.execute(
                """
                UPDATE empleados
                SET nombre = ?, apellidos = ?, email = ?, telefono = ?,
                    observaciones = ?, motivo_cambio = ?
                WHERE id = ?
                """,
                (
                    nombre,
                    apellidos,
                    email,
                    telefono,
                    f"Empleado simulado Hotel 1. Rol: {row['rol'] or '-'}",
                    "Normalizacion de plantilla simulada con nombres unicos",
                    row["id"],
                ),
            )
        conn.commit()
        print(f"Normalizados {len(rows)} empleados simulados en {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

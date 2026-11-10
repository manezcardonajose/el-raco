# -*- coding: utf-8 -*-
"""Crea el escenario Hotel Albarracín como empresa/centro independiente.

Base pensada para analizar rentabilidad y operativa antes de cargar datos reales.
Es idempotente: si ya existe, actualiza lo basico y no duplica habitaciones ni
empleados simulados.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"


ROOM_TYPES = [
    ("IND", "Habitación individual estándar", 1, 1, "Una cama. Tipo oficial web Hotel Albarracín."),
    ("DOB", "Habitación doble, twin o doble uso individual", 2, 2, "Dos camas o cama de matrimonio bajo disponibilidad."),
    ("TRP", "Habitación triple", 3, 3, "Tres camas. Familiar o amigos."),
    ("CUA", "Habitación cuádruple", 4, 4, "Cuatro camas. Incluye posibilidad de PMR según disponibilidad real."),
    ("PAN", "Habitación doble con vistas panorámicas", 2, 2, "Doble con vistas al perfil histórico de Albarracín."),
    ("MIR", "Habitación doble del mirador", 2, 2, "Doble con cama de matrimonio y vistas."),
    ("REI", "Habitación doble de la Reina", 2, 2, "Doble especial con cama de matrimonio y teléfono en baño."),
]


ROOM_PLAN = (
    [("IND", 4), ("DOB", 10)]
    + [("DOB", 10), ("TRP", 3), ("CUA", 1)]
    + [("DOB", 4), ("TRP", 2), ("CUA", 1), ("PAN", 3), ("MIR", 2), ("REI", 1)]
)


DEPARTMENTS = [
    ("DIRECCION", "general", 0, 1, 0, ["Direccion operativa", "Administracion"]),
    ("RECEPCION", "recepcion", 0, 0, 0, ["Reservas", "Check-in / Check-out", "Recepcion 24h"]),
    ("PISOS", "pisos", 1, 1, 0, ["Habitaciones", "Zonas comunes", "Lenceria"]),
    ("COCINA", "cocina", 1, 1, 0, ["Cocina caliente", "Cuarto frio", "Desayunos", "Office"]),
    ("RESTAURANTE / BAR", "restaurante", 1, 1, 0, ["Restaurante", "Bar cafeteria", "Desayuno buffet"]),
    ("MANTENIMIENTO", "mantenimiento", 1, 1, 1, ["Preventivo", "Correctivo", "Aguas y legionella"]),
    ("COMPRAS / ALMACEN", "almacen", 1, 1, 0, ["Economato", "Recepcion mercancias"]),
    ("RRHH", "general", 0, 0, 0, ["Formacion PRL y alimentaria"]),
]


EMPLOYEES = [
    ("Marta", "Gargallo Royo", "Direccion", "DIRECCION", "Direccion operativa"),
    ("Sergio", "Beltran Marco", "Subdireccion / administracion", "DIRECCION", "Administracion"),
    ("Ana", "Lahoz Fuertes", "Jefa recepcion", "RECEPCION", "Recepcion 24h"),
    ("Javier", "Navarro Polo", "Recepcionista manana", "RECEPCION", "Check-in / Check-out"),
    ("Laura", "Martin Bielsa", "Recepcionista tarde", "RECEPCION", "Check-in / Check-out"),
    ("Diego", "Sanz Villalba", "Recepcionista noche", "RECEPCION", "Recepcion 24h"),
    ("Clara", "Valero Moya", "Recepcionista rotativa", "RECEPCION", "Reservas"),
    ("Pilar", "Alegre Soriano", "Gobernanta", "PISOS", "Habitaciones"),
    ("Rosa", "Montero Abril", "Camarera pisos", "PISOS", "Habitaciones"),
    ("Nuria", "Serrano Gil", "Camarera pisos", "PISOS", "Habitaciones"),
    ("Beatriz", "Marco Esteban", "Camarera pisos rotativa", "PISOS", "Habitaciones"),
    ("Elena", "Pastor Civera", "Zonas comunes y lenceria", "PISOS", "Zonas comunes"),
    ("Hector", "Gimeno Andres", "Jefe cocina", "COCINA", "Cocina caliente"),
    ("Isabel", "Cortes Blasco", "Cocinera", "COCINA", "Cocina caliente"),
    ("Raul", "Soriano Abril", "Ayudante cocina", "COCINA", "Cuarto frio"),
    ("Mina", "Benali Rachid", "Office / plonge", "COCINA", "Office"),
    ("Tomas", "Andreu Soler", "Jefe sala", "RESTAURANTE / BAR", "Restaurante"),
    ("Lucia", "Campos Ferrer", "Camarera restaurante", "RESTAURANTE / BAR", "Restaurante"),
    ("Mario", "Ibanez Gracia", "Camarero bar", "RESTAURANTE / BAR", "Bar cafeteria"),
    ("Sara", "Pardo Vidal", "Camarera rotativa", "RESTAURANTE / BAR", "Desayuno buffet"),
    ("Oscar", "Lacruz Mateo", "Tecnico mantenimiento", "MANTENIMIENTO", "Preventivo"),
    ("Noelia", "Ramos Fuertes", "Compras y almacen", "COMPRAS / ALMACEN", "Recepcion mercancias"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crear empresa Gargallo y centro Hotel Albarracín.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def get_or_create_empresa(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM empresas WHERE LOWER(nombre)=LOWER(?)",
        ("Gargallo",),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO empresas (nombre, nif, direccion, tel?fono, email, activo)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            "Gargallo",
            None,
            "Albarracín, Teruel",
            None,
            None,
        ),
    )
    return int(cur.lastrowid)


def get_or_create_centro(conn: sqlite3.Connection, empresa_id: int) -> int:
    row = conn.execute(
        """
        SELECT id FROM centros_trabajo
        WHERE empresa_id=? AND codigo=?
        """,
        (empresa_id, "ALBARRACIN"),
    ).fetchone()
    if row:
        centro_id = int(row["id"])
        conn.execute(
            """
            UPDATE centros_trabajo
            SET nombre='Hotel Albarracín', activo=1, codigo='ALBARRACIN', direccion='C/ Azagra s/n, Albarracín, Teruel',
                descripcion='Hotel Albarracín. Escenario independiente para analisis OPS/Raco: 41 habitaciones, temporadas Imserso y temporada comercial.'
            WHERE id=?
            """,
            (centro_id,),
        )
        return centro_id
    cur = conn.execute(
        """
        INSERT INTO centros_trabajo
        (nombre, codigo, direccion, descripcion, empresa_id, activo)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            "Hotel Albarracín",
            "ALBARRACIN",
            "C/ Azagra s/n, Albarracín, Teruel",
            "Escenario independiente para analisis OPS/Raco: 41 habitaciones, temporadas Imserso y temporada comercial.",
            empresa_id,
        ),
    )
    return int(cur.lastrowid)


def get_or_create_department(conn: sqlite3.Connection, empresa_id: int, centro_id: int, name: str, tipo: str, appcc: int, stock: int, mantenimiento: int) -> int:
    row = conn.execute(
        """
        SELECT id FROM departamentos
        WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER(?)
        """,
        (empresa_id, centro_id, name),
    ).fetchone()
    if row:
        dep_id = int(row["id"])
        conn.execute(
            """
            UPDATE departamentos
            SET activo=1, tipo_operativo=?, gestiona_appcc=?, gestiona_stock=?, gestiona_mantenimiento=?
            WHERE id=?
            """,
            (tipo, appcc, stock, mantenimiento, dep_id),
        )
        return dep_id
    cur = conn.execute(
        """
        INSERT INTO departamentos
        (nombre, descripcion, activo, empresa_id, centro_id, tipo_operativo,
         gestiona_appcc, gestiona_stock, gestiona_mantenimiento)
        VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
        """,
        (name, f"Departamento operativo {name} - Hotel Albarracín", empresa_id, centro_id, tipo, appcc, stock, mantenimiento),
    )
    return int(cur.lastrowid)


def get_or_create_subdepartment(conn: sqlite3.Connection, empresa_id: int, centro_id: int, dep_id: int, name: str) -> int:
    row = conn.execute(
        """
        SELECT id FROM subdepartamentos
        WHERE empresa_id=? AND centro_id=? AND departamento_id=? AND LOWER(nombre)=LOWER(?)
        """,
        (empresa_id, centro_id, dep_id, name),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO subdepartamentos
        (nombre, departamento_id, descripcion, activo, empresa_id, centro_id)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (name, dep_id, f"Subdepartamento {name} - Hotel Albarracín", empresa_id, centro_id),
    )
    return int(cur.lastrowid)


def seed_departments(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    deps: dict[str, int] = {}
    subs: dict[tuple[str, str], int] = {}
    for name, tipo, appcc, stock, mantenimiento, subnames in DEPARTMENTS:
        dep_id = get_or_create_department(conn, empresa_id, centro_id, name, tipo, appcc, stock, mantenimiento)
        deps[name] = dep_id
        for sub in subnames:
            subs[(name, sub)] = get_or_create_subdepartment(conn, empresa_id, centro_id, dep_id, sub)
    return deps, subs


def seed_room_types(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> dict[str, int]:
    ids: dict[str, int] = {}
    for codigo, nombre, base, max_cap, desc in ROOM_TYPES:
        row = conn.execute(
            """
            SELECT id FROM recepcion_tipos_habitacion
            WHERE empresa_id=? AND centro_id=? AND codigo=?
            """,
            (empresa_id, centro_id, codigo),
        ).fetchone()
        if row:
            tipo_id = int(row["id"])
            conn.execute(
                """
                UPDATE recepcion_tipos_habitacion
                SET nombre=?, capacidad_base=?, capacidad_max=?, descripcion=?, activo=1
                WHERE id=?
                """,
                (nombre, base, max_cap, desc, tipo_id),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO recepcion_tipos_habitacion
                (codigo, nombre, capacidad_base, capacidad_max, descripcion, activo, empresa_id, centro_id)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (codigo, nombre, base, max_cap, desc, empresa_id, centro_id),
            )
            tipo_id = int(cur.lastrowid)
        ids[codigo] = tipo_id
    return ids


def seed_rooms(conn: sqlite3.Connection, empresa_id: int, centro_id: int, type_ids: dict[str, int]) -> int:
    type_meta = {codigo: (nombre, cap) for codigo, nombre, _base, cap, _desc in ROOM_TYPES}
    existing = conn.execute(
        "SELECT COUNT(*) total FROM habitaciones_recepcion WHERE empresa_id=? AND centro_id=?",
        (empresa_id, centro_id),
    ).fetchone()["total"]
    if int(existing or 0) >= 41:
        for codigo, tipo_id in type_ids.items():
            tipo_nombre, capacidad = type_meta[codigo]
            conn.execute(
                """
                UPDATE habitaciones_recepcion
                SET nombre=?, tipo=?, capacidad=?
                WHERE empresa_id=? AND centro_id=? AND tipo_habitacion_id=?
                """,
                (tipo_nombre, tipo_nombre, capacidad, empresa_id, centro_id, tipo_id),
            )
        return 0

    plan: list[str] = []
    for codigo, count in ROOM_PLAN:
        plan.extend([codigo] * count)
    assert len(plan) == 41

    inserted = 0
    for idx, codigo in enumerate(plan, start=1):
        planta = 1 if idx <= 14 else 2 if idx <= 28 else 3
        numero = f"{planta}{(idx - 1) % 14 + 1:02d}"
        if conn.execute(
            """
            SELECT 1 FROM habitaciones_recepcion
            WHERE empresa_id=? AND centro_id=? AND numero=?
            """,
            (empresa_id, centro_id, numero),
        ).fetchone():
            continue
        tipo_nombre, capacidad = type_meta[codigo]
        conn.execute(
            """
            INSERT INTO habitaciones_recepcion
            (numero, nombre, tipo, planta, capacidad, estado, centro_id, observaciones,
             empresa_id, activo, tipo_habitacion_id, bloque)
            VALUES (?, ?, ?, ?, ?, 'disponible', ?, ?, ?, 1, ?, 'Palacio Brigadiera')
            """,
            (
                numero,
                tipo_nombre,
                tipo_nombre,
                str(planta),
                capacidad,
                centro_id,
                "Distribución provisional: 41 habitaciones total. Ajustar cuando tengamos inventario real por tipo.",
                empresa_id,
                type_ids[codigo],
            ),
        )
        inserted += 1
    return inserted


def get_or_create_category(conn: sqlite3.Connection, empresa_id: int, rol: str) -> int:
    row = conn.execute(
        """
        SELECT id FROM categorias_laborales
        WHERE empresa_id=? AND LOWER(nombre)=LOWER(?)
        """,
        (empresa_id, rol),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO categorias_laborales
        (nombre, descripcion, convenio, empresa_id, activo)
        VALUES (?, ?, 'Hosteleria - provisional simulacion', ?, 1)
        """,
        (rol, f"Categoría provisional para simulación de rentabilidad Hotel Albarracín", empresa_id),
    )
    return int(cur.lastrowid)


def seed_employees(conn: sqlite3.Connection, empresa_id: int, centro_id: int, deps: dict[str, int], subs: dict[tuple[str, str], int]) -> int:
    inserted = 0
    for nombre, apellidos, rol, dep_name, sub_name in EMPLOYEES:
        code = f"ALB-{nombre[:2]}{apellidos[:2]}-{rol[:4]}".upper().replace(" ", "")
        if conn.execute(
            """
            SELECT 1 FROM empleados
            WHERE empresa_id=? AND centro_id=? AND codigo_importacion=?
            """,
            (empresa_id, centro_id, code),
        ).fetchone():
            continue
        categoria_id = get_or_create_category(conn, empresa_id, rol)
        conn.execute(
            """
            INSERT INTO empleados
            (nombre, apellidos, rol, departamento_id, subdepartamento_id, categoria_id,
             activo, empresa_id, centro_id, tipo_empleado, origen_importacion,
             codigo_importacion, carnet_manipulador, prl_realizada, reconocimiento_medico,
             observaciones)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 'simulado', 'seed_albarracin_gargallo',
                    ?, ?, ?, 0, ?)
            """,
            (
                nombre,
                apellidos,
                rol,
                deps[dep_name],
                subs[(dep_name, sub_name)],
                categoria_id,
                empresa_id,
                centro_id,
                code,
                1 if dep_name in {"COCINA", "RESTAURANTE / BAR"} else 0,
                1,
                "Empleado simulado para dimensionar plantilla, descansos, temporada Imserso y rentabilidad.",
            ),
        )
        inserted += 1
    return inserted


def ensure_temporadas_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_temporadas_centro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER,
            centro_id INTEGER,
            nombre TEXT,
            fecha_inicio TEXT,
            fecha_fin TEXT,
            segmento TEXT,
            ocupacion_objetivo REAL,
            adr_objetivo REAL,
            notas TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, centro_id, nombre, fecha_inicio)
        )
        """
    )


def seed_temporadas(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> int:
    ensure_temporadas_table(conn)
    rows = [
        ("Imserso primavera", "2027-02-15", "2027-04-30", "imserso", 0.78, 58.0, "Temporada social con margen ajustado; controlar coste de pension completa."),
        ("Temporada comercial verano-otono", "2027-05-01", "2027-10-31", "comercial", 0.72, 92.0, "Demanda turística Albarracín/Teruel; ajustar precio por fines de semana y eventos."),
        ("Imserso o grupos otono-invierno", "2027-11-01", "2027-12-20", "imserso_grupos", 0.65, 55.0, "Segunda ventana de grupos/Imserso si contrato compensa costes."),
        ("Baja demanda tecnica", "2027-12-21", "2028-02-14", "baja", 0.35, 70.0, "Periodo a estudiar: abrir parcial, cerrar dias flojos o vender escapadas."),
    ]
    inserted = 0
    for row in rows:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO simulador_temporadas_centro
            (empresa_id, centro_id, nombre, fecha_inicio, fecha_fin, segmento,
             ocupacion_objetivo, adr_objetivo, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (empresa_id, centro_id, *row),
        )
        inserted += cur.rowcount
    return inserted


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        empresa_id = get_or_create_empresa(conn)
        centro_id = get_or_create_centro(conn, empresa_id)
        deps, subs = seed_departments(conn, empresa_id, centro_id)
        type_ids = seed_room_types(conn, empresa_id, centro_id)
        habitaciones = seed_rooms(conn, empresa_id, centro_id, type_ids)
        empleados = seed_employees(conn, empresa_id, centro_id, deps, subs)
        temporadas = seed_temporadas(conn, empresa_id, centro_id)
        conn.commit()
        resumen = {
            "empresa_id": empresa_id,
            "centro_id": centro_id,
            "empresa": "Gargallo",
            "centro": "Hotel Albarracín",
            "habitaciones_nuevas": habitaciones,
            "empleados_nuevos": empleados,
            "temporadas_nuevas": temporadas,
            "tipos_habitacion": len(type_ids),
            "departamentos": len(deps),
            "subdepartamentos": len(subs),
        }
        print(resumen)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

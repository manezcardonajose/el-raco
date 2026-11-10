# -*- coding: utf-8 -*-
"""Inyecta casos feos de RRHH en Hotel 1 sin borrar datos."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


CASOS = [
    ("no_se_presenta", "No ficha entrada ni salida", None, None, None),
    ("sin_salida", "Ficha entrada pero no salida", -2, None, None),
    ("sin_entrada", "Ficha salida pero no entrada", None, 4, None),
    ("entrada_anticipada", "Entra 55 minutos antes", -55, 0, None),
    ("salida_tardia", "Sale 70 minutos tarde", 0, 70, None),
    ("salida_anticipada", "Sale 90 minutos antes", 0, -90, None),
    ("ausencia_justificada", "Ausencia aprobada con turno", None, None, "ausencia_justificada"),
    ("baja_medica", "Baja medica aprobada con turno", None, None, "baja_medica"),
    ("fichaje_sin_turno", "Ficha en dia sin turno asignado", 0, 0, None),
    ("doble_turno", "Dos turnos el mismo dia para el mismo empleado", 0, 15, None),
    ("turno_partido_incompleto", "Turno partido con solo primer tramo fichado", 0, None, None),
    ("pausa_sin_reanudar", "Pausa registrada sin reanudacion", 0, 0, None),
]


NOMBRES = [
    ("Laura", "Fichaje Prueba"),
    ("Mario", "Ausencias Control"),
    ("Sonia", "Turnos Partidos"),
    ("Ruben", "No Presenta"),
    ("Isabel", "Salida Tardia"),
    ("Nuria", "Pausa Abierta"),
    ("Oscar", "Sin Salida"),
    ("Claudia", "Sin Entrada"),
    ("Hector", "Doble Turno"),
    ("Marta", "Baja Medica"),
    ("Pablo", "Extra Pendiente"),
    ("Celia", "Libre Fichado"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inyectar prueba de fuego RRHH Hotel 1.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--fecha", default="2026-06-10")
    parser.add_argument("--corrida-id", type=int, default=0)
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
        CREATE TABLE IF NOT EXISTS simulador_rrhh_estres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha TEXT,
            empleado_id INTEGER,
            turno_id INTEGER,
            caso TEXT,
            descripcion TEXT,
            resultado_esperado TEXT,
            estado_revision TEXT DEFAULT 'pendiente',
            evidencia_json TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fecha, empleado_id, caso, turno_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_rrhh_recomendaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha TEXT,
            area TEXT,
            prioridad TEXT,
            titulo TEXT,
            recomendacion TEXT,
            impacto TEXT,
            evidencia_json TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def find_id(conn: sqlite3.Connection, table: str, names: list[str]) -> int | None:
    for name in names:
        row = conn.execute(
            f"SELECT id FROM {table} WHERE empresa_id=? AND LOWER(nombre)=LOWER(?) ORDER BY id LIMIT 1",
            (EMPRESA_ID, name),
        ).fetchone()
        if row:
            return int(row["id"])
    return None


def ensure_employee(conn: sqlite3.Connection, idx: int, dep_id: int | None, sub_id: int | None, cat_id: int | None) -> int:
    nombre, apellidos = NOMBRES[idx % len(NOMBRES)]
    codigo = f"RRHH-ESTRES-H1-{idx+1:02d}"
    row = conn.execute(
        "SELECT id FROM empleados WHERE empresa_id=? AND codigo_importacion=?",
        (EMPRESA_ID, codigo),
    ).fetchone()
    if row:
        return int(row["id"])
    email = f"rrhh.estres{idx+1:02d}@hotel1.demo"
    cur = conn.execute(
        """
        INSERT INTO empleados
        (nombre, apellidos, email, departamento_id, subdepartamento_id, categoria_id,
         rol, activo, empresa_id, centro_id, tipo_empleado, origen_importacion,
         codigo_importacion, fecha_alta, observaciones)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 3, 'simulacion', 'simulador_rrhh_estres',
                ?, ?, ?)
        """,
        (
            nombre,
            apellidos,
            email,
            dep_id,
            sub_id,
            cat_id,
            "Prueba RRHH",
            codigo,
            "2026-05-27",
            "Empleado creado para prueba de fuego de fichajes, ausencias y cuadrantes.",
        ),
    )
    return int(cur.lastrowid)


def insert_turno(conn: sqlite3.Connection, empleado_id: int, fecha: date, inicio: str, fin: str, dep_id: int | None, sub_id: int | None, caso: str) -> int:
    row = conn.execute(
        """
        SELECT id FROM turnos_rrhh
        WHERE empleado_id=? AND fecha=? AND hora_inicio=? AND hora_fin=?
          AND origen='simulador_rrhh_estres' AND empresa_id=1
        LIMIT 1
        """,
        (empleado_id, fecha.isoformat(), inicio, fin),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO turnos_rrhh
        (empleado_id, fecha, hora_inicio, hora_fin, tipo, departamento_id,
         subdepartamento_id, centro_id, estado, origen, observaciones, empresa_id)
        VALUES (?, ?, ?, ?, 'real', ?, ?, 3, 'planificado',
                'simulador_rrhh_estres', ?, 1)
        """,
        (empleado_id, fecha.isoformat(), inicio, fin, dep_id, sub_id, f"Prueba RRHH: {caso}"),
    )
    return int(cur.lastrowid)


def insert_fichaje(conn: sqlite3.Connection, empleado_id: int, tipo: str, dt: datetime, turno_id: int | None, obs: str) -> int:
    digest = hashlib.sha256(f"{empleado_id}|{tipo}|{dt.isoformat()}|rrhh_estres".encode("utf-8")).hexdigest()
    row = conn.execute("SELECT id FROM fichajes WHERE hash_registro=? LIMIT 1", (digest,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO fichajes
        (empleado_id, tipo, fecha_hora, origen, observaciones, empresa_id, turno_id,
         estado, minutos_desviacion, aprobado_extra, ip_origen, user_agent, hash_registro)
        VALUES (?, ?, ?, 'simulador_rrhh_estres', ?, 1, ?, 'activo', 0, 0,
                '127.0.0.1', 'simulador_rrhh_estres', ?)
        """,
        (empleado_id, tipo, dt.strftime("%Y-%m-%d %H:%M:%S"), obs, turno_id, digest),
    )
    return int(cur.lastrowid)


def insert_absence(conn: sqlite3.Connection, empleado_id: int, fecha: date, tipo: str, descripcion: str) -> None:
    row = conn.execute(
        """
        SELECT id FROM vacaciones_rrhh
        WHERE empleado_id=? AND fecha_inicio=? AND fecha_fin=? AND tipo=? AND origen='simulador_rrhh_estres'
        """,
        (empleado_id, fecha.isoformat(), fecha.isoformat(), tipo),
    ).fetchone()
    if row:
        return
    conn.execute(
        """
        INSERT INTO vacaciones_rrhh
        (empleado_id, fecha_inicio, fecha_fin, tipo, estado, dias, observaciones,
         empresa_id, origen, afecta_prenomina, porcentaje_cobro, codigo_oficial)
        VALUES (?, ?, ?, ?, 'aprobada', 1, ?, 1, 'simulador_rrhh_estres', 1, ?, ?)
        """,
        (
            empleado_id,
            fecha.isoformat(),
            fecha.isoformat(),
            tipo,
            descripcion,
            75 if tipo == "baja_medica" else 100,
            tipo.upper(),
        ),
    )


def insert_variable(conn: sqlite3.Connection, empleado_id: int, fecha: date, caso: str, amount: float) -> None:
    concepto = f"Simulador RRHH - {caso}"
    row = conn.execute(
        """
        SELECT id FROM variables_prenomina
        WHERE empleado_id=? AND fecha=? AND concepto=? AND empresa_id=1
        """,
        (empleado_id, fecha.isoformat(), concepto),
    ).fetchone()
    if row:
        return
    conn.execute(
        """
        INSERT INTO variables_prenomina
        (empleado_id, fecha, concepto, tipo, unidades, importe_unitario,
         importe_total, observaciones, empresa_id, estado)
        VALUES (?, ?, ?, 'ajuste', 1, ?, ?, 'Generada por simulador RRHH estres', 1, 'pendiente')
        """,
        (empleado_id, fecha.isoformat(), concepto, amount, amount),
    )


def register_case(conn: sqlite3.Connection, corrida_id: int, fecha: date, empleado_id: int, turno_id: int | None, caso: str, descripcion: str, esperado: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO simulador_rrhh_estres
        (corrida_id, fecha, empleado_id, turno_id, caso, descripcion,
         resultado_esperado, evidencia_json, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, '{}', 1, 3)
        """,
        (corrida_id, fecha.isoformat(), empleado_id, turno_id, caso, descripcion, esperado),
    )


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    base_date = date.fromisoformat(args.fecha)
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        corrida_id = args.corrida_id or latest_corrida(conn)
        dep_id = find_id(conn, "departamentos", ["COCINA CENTRAL", "BARES/RESTAURANTES", "PISOS", "RECEPCION"]) or 1
        sub_id = find_id(conn, "subdepartamentos", ["Cocina caliente", "Bar piscina", "LIMPIEZA HABITACIONES", "CHECK-IN / CHECK-OUT"])
        cat_id = find_id(conn, "categorias_laborales", ["Camarero", "Cocinero", "Ayudante cocina"]) or 1
        created = 0
        for idx, (caso, descripcion, entrada_offset, salida_offset, ausencia_tipo) in enumerate(CASOS):
            fecha = base_date + timedelta(days=idx % 6)
            empleado_id = ensure_employee(conn, idx, dep_id, sub_id, cat_id)
            if caso == "fichaje_sin_turno":
                entrada = datetime.combine(fecha, time(10, 0))
                insert_fichaje(conn, empleado_id, "entrada", entrada, None, descripcion)
                insert_fichaje(conn, empleado_id, "salida", entrada + timedelta(hours=8), None, descripcion)
                register_case(conn, corrida_id, fecha, empleado_id, None, caso, descripcion, "Debe aparecer como fichaje sin turno al cruzar datos.")
                created += 1
                continue
            turno_id = insert_turno(conn, empleado_id, fecha, "08:00", "16:00", dep_id, sub_id, caso)
            if ausencia_tipo:
                insert_absence(conn, empleado_id, fecha, ausencia_tipo, descripcion)
            if entrada_offset is not None:
                insert_fichaje(conn, empleado_id, "entrada", datetime.combine(fecha, time(8, 0)) + timedelta(minutes=entrada_offset), turno_id, descripcion)
            if salida_offset is not None:
                insert_fichaje(conn, empleado_id, "salida", datetime.combine(fecha, time(16, 0)) + timedelta(minutes=salida_offset), turno_id, descripcion)
            if caso == "doble_turno":
                insert_turno(conn, empleado_id, fecha, "17:00", "23:00", dep_id, sub_id, caso)
            if caso == "turno_partido_incompleto":
                insert_turno(conn, empleado_id, fecha, "19:00", "23:00", dep_id, sub_id, caso)
            if caso == "pausa_sin_reanudar":
                insert_fichaje(conn, empleado_id, "pausa", datetime.combine(fecha, time(12, 0)), turno_id, descripcion)
            if caso in {"entrada_anticipada", "salida_tardia"}:
                insert_variable(conn, empleado_id, fecha, caso, 18.50)
            esperado = "Debe disparar alerta de RRHH/fichajes y revisarse antes de pre-nomina."
            if ausencia_tipo:
                esperado = "Debe aparecer como ausencia registrada y no como falta injustificada."
            register_case(conn, corrida_id, fecha, empleado_id, turno_id, caso, descripcion, esperado)
            created += 1
        conn.execute("DELETE FROM simulador_rrhh_recomendaciones WHERE corrida_id=? AND fecha=?", (corrida_id, base_date.isoformat()))
        conn.execute(
            """
            INSERT INTO simulador_rrhh_recomendaciones
            (corrida_id, fecha, area, prioridad, titulo, recomendacion, impacto, evidencia_json)
            VALUES (?, ?, 'rrhh', 'alta', 'Prueba de fuego de fichajes Hotel 1',
                    'Revisar alertas por dia, justificar ausencias, aprobar/rechazar extras y comprobar que pre-nomina no pague ruido sin validar.',
                    'Evita pagar horas no aprobadas, detecta absentismo y mejora cobertura real del servicio.',
                    ?)
            """,
            (corrida_id, base_date.isoformat(), f'{{"casos": {created}}}'),
        )
        conn.commit()
        print(f"RRHH estres inyectado sin borrar: {created} casos desde {base_date.isoformat()} en {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

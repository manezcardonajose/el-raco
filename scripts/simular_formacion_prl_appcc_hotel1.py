# -*- coding: utf-8 -*-
"""Simula formacion PRL/APPCC y carnets de manipulador con fallos controlados."""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simular formacion PRL y manipulador en Hotel 1.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260602)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def area_operativa(row: sqlite3.Row) -> str:
    texto = " ".join(str(row[k] or "") for k in row.keys() if k in {"rol", "departamento", "subdepartamento"}).lower()
    if any(p in texto for p in ["cocina", "plonge", "chef", "caliente", "frio"]):
        return "cocina"
    if any(p in texto for p in ["bar", "bares", "restaurante", "sala", "buffet", "camarer"]):
        return "sala"
    if any(p in texto for p in ["piso", "gobernanta", "limpieza"]):
        return "pisos"
    if any(p in texto for p in ["mantenimiento", "tecnico"]):
        return "mantenimiento"
    if any(p in texto for p in ["recepcion", "reservas", "administracion", "night audit", "auditor"]):
        return "recepcion"
    if any(p in texto for p in ["animacion", "animador", "eventos", "monitor", "miniclub"]):
        return "animacion"
    return "general"


def ensure_formaciones(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> None:
    if not table_exists(conn, "prl_formaciones"):
        return
    formaciones = [
        ("PRL-FOR-COC", "PRL cocina industrial", "Cocina", 45, "Cortes, quemaduras, suelos mojados, maquinaria, quimicos, biologico y emergencias."),
        ("PRL-FOR-MANIP", "Manipulador de alimentos y alergenos", "Alimentaria", 60, "Higiene personal, contaminacion cruzada, temperaturas, alergenos, heridas, lotes, etiquetado y buenas practicas APPCC."),
        ("PRL-FOR-SAL", "PRL sala, bar y buffet", "Sala y bares", 35, "Derrames, carga de bandejas, flujo de clientes, terrazas, conflictos y evacuacion."),
        ("PRL-FOR-PIS", "PRL pisos y limpieza", "Pisos", 45, "Ergonomia, carros, productos quimicos, banos, camas y comunicacion de incidencias."),
        ("PRL-FOR-MAN", "PRL mantenimiento hotelero", "Mantenimiento", 60, "Electricidad, altura, herramientas, sala maquinas, legionela y permisos de trabajo."),
        ("PRL-FOR-REC", "PRL recepcion y administracion", "Recepcion", 35, "Pantallas, ergonomia, nocturnidad, clientes conflictivos, proteccion de datos y emergencias."),
        ("PRL-FOR-ANI", "PRL animacion, eventos y actividades", "Animacion", 45, "Montajes, escenarios, sonido, piscina, menores, calor, clientes conflictivos y evacuacion."),
        ("PRL-FOR-APPCC", "APPCC conectado con seguridad laboral", "General", 40, "Riesgo biologico, higiene, residuos, heridas, trazabilidad y seguridad alimentaria."),
        ("PRL-FOR-EME", "Emergencias, incendios y evacuacion", "General", 35, "Actuacion ante incendio, evacuacion, primeros avisos y puntos de encuentro."),
    ]
    for codigo, titulo, area, duracion, contenido in formaciones:
        conn.execute(
            """
            INSERT OR IGNORE INTO prl_formaciones
            (codigo, titulo, area, duracion_minutos, periodicidad, contenido,
             evaluacion, obligatoria, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, 'anual', ?, 'Cuestionario corto y evidencia de asistencia.', 1, ?, ?)
            """,
            (codigo, titulo, area, duracion, contenido, empresa_id, centro_id),
        )


def codigos_requeridos(area: str) -> set[str]:
    base = {"PRL-FOR-APPCC", "PRL-FOR-EME"}
    por_area = {
        "cocina": {"PRL-FOR-COC", "PRL-FOR-MANIP"},
        "sala": {"PRL-FOR-SAL", "PRL-FOR-MANIP"},
        "pisos": {"PRL-FOR-PIS"},
        "mantenimiento": {"PRL-FOR-MAN"},
        "recepcion": {"PRL-FOR-REC"},
        "animacion": {"PRL-FOR-ANI"},
    }
    return base | por_area.get(area, set())


def asignar_formaciones(conn: sqlite3.Connection, empleados: list[sqlite3.Row], empresa_id: int, centro_id: int) -> int:
    if not (table_exists(conn, "prl_formaciones") and table_exists(conn, "prl_formacion_empleados")):
        return 0
    formaciones = {
        row["codigo"]: row["id"]
        for row in conn.execute(
            "SELECT id, codigo FROM prl_formaciones WHERE empresa_id=? AND centro_id=? AND COALESCE(activo,1)=1",
            (empresa_id, centro_id),
        ).fetchall()
    }
    asignadas = 0
    for emp in empleados:
        for codigo in codigos_requeridos(area_operativa(emp)):
            formacion_id = formaciones.get(codigo)
            if not formacion_id:
                continue
            before = conn.total_changes
            conn.execute(
                """
                INSERT OR IGNORE INTO prl_formacion_empleados
                (formacion_id, empleado_id, estado, empresa_id, centro_id)
                VALUES (?, ?, 'pendiente', ?, ?)
                """,
                (formacion_id, emp["id"], empresa_id, centro_id),
            )
            if conn.total_changes > before:
                asignadas += 1
    return asignadas


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        if not table_exists(conn, "empleados"):
            raise SystemExit("No hay empleados.")
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
        ensure_formaciones(conn, args.empresa_id, args.centro_id)
        asignadas = asignar_formaciones(conn, empleados, args.empresa_id, args.centro_id)
        hoy = date(2026, 6, 2)
        prl_ok = carnet_ok = carnet_caducado = 0
        for emp in empleados:
            area = area_operativa(emp)
            if area in {"cocina", "sala", "pisos", "mantenimiento", "recepcion", "animacion"} and random.random() < 0.78:
                fecha_prl = hoy - timedelta(days=random.randint(5, 320))
                conn.execute(
                    "UPDATE empleados SET prl_realizada=1, fecha_prl=? WHERE id=? AND empresa_id=?",
                    (fecha_prl.isoformat(), emp["id"], args.empresa_id),
                )
                prl_ok += 1
            if area in {"cocina", "sala"}:
                roll = random.random()
                if roll < 0.72:
                    cad = hoy + timedelta(days=random.randint(45, 520))
                    conn.execute(
                        "UPDATE empleados SET carnet_manipulador=1, fecha_caducidad_carnet=? WHERE id=? AND empresa_id=?",
                        (cad.isoformat(), emp["id"], args.empresa_id),
                    )
                    carnet_ok += 1
                elif roll < 0.86:
                    cad = hoy - timedelta(days=random.randint(1, 90))
                    conn.execute(
                        "UPDATE empleados SET carnet_manipulador=1, fecha_caducidad_carnet=? WHERE id=? AND empresa_id=?",
                        (cad.isoformat(), emp["id"], args.empresa_id),
                    )
                    carnet_caducado += 1
        if table_exists(conn, "prl_formacion_empleados"):
            rows = conn.execute(
                """
                SELECT pfe.id, f.codigo
                FROM prl_formacion_empleados pfe
                JOIN prl_formaciones f ON f.id=pfe.formacion_id
                WHERE pfe.empresa_id=? AND pfe.centro_id=? AND pfe.estado='pendiente'
                """,
                (args.empresa_id, args.centro_id),
            ).fetchall()
            realizadas = 0
            for row in rows:
                prob = 0.74 if row["codigo"] in {"PRL-FOR-MANIP", "PRL-FOR-APPCC", "PRL-FOR-EME"} else 0.66
                if row["codigo"] in {"PRL-FOR-REC", "PRL-FOR-ANI"}:
                    prob = 0.58
                if random.random() < prob:
                    fecha = hoy - timedelta(days=random.randint(1, 260))
                    nota = round(random.uniform(6.0, 9.8), 1)
                    conn.execute(
                        """
                        UPDATE prl_formacion_empleados
                        SET estado='realizada', fecha_realizacion=?, nota=?, evidencia=?
                        WHERE id=?
                        """,
                        (fecha.isoformat(), nota, "simulacion_formacion_prl_appcc_hotel1", row["id"]),
                    )
                    realizadas += 1
        else:
            realizadas = 0
        conn.commit()
        print({
            "formaciones_asignadas": asignadas,
            "prl_realizada": prl_ok,
            "carnet_vigente": carnet_ok,
            "carnet_caducado": carnet_caducado,
            "formaciones_realizadas": realizadas,
        })
    finally:
        conn.close()


if __name__ == "__main__":
    main()

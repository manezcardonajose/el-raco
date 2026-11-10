# -*- coding: utf-8 -*-
"""Aplica economia configurable de Imserso a la simulacion de Albarracin."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
EMPRESA_ID = 2
CENTRO_ID = 8
ORIGEN = "simulador_albarracin_ocupacion"


DEFAULTS = {
    "operador": "Mundiplan",
    "importe_pax_dia": 45.00,
    "suplemento_individual_dia": 25.00,
    "punto_equilibrio_pax_dia": 35.00,
    "presupuesto_comida_pax_dia": 10.50,
    "presupuesto_personal_pax_dia": 6.50,
    "presupuesto_limpieza_lenceria_pax_dia": 2.25,
    "presupuesto_suministros_pax_dia": 2.25,
    "presupuesto_mantenimiento_pax_dia": 1.00,
    "presupuesto_alojamiento_estructura_pax_dia": 3.25,
    "margen_objetivo_pax_dia": 1.75,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aplicar economia Imserso Albarracin.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--year", type=int, default=2027)
    parser.add_argument("--importe-pax-dia", type=float, default=DEFAULTS["importe_pax_dia"])
    parser.add_argument("--suplemento-individual-dia", type=float, default=DEFAULTS["suplemento_individual_dia"])
    parser.add_argument("--punto-equilibrio-pax-dia", type=float, default=DEFAULTS["punto_equilibrio_pax_dia"])
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def daterange(start: date, end: date):
    cur = start
    while cur < end:
        yield cur
        cur += timedelta(days=1)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS imserso_contrato_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER,
            centro_id INTEGER,
            year INTEGER,
            operador TEXT DEFAULT 'Mundiplan',
            importe_pax_dia REAL,
            suplemento_individual_dia REAL DEFAULT 0,
            punto_equilibrio_pax_dia REAL,
            presupuesto_comida_pax_dia REAL,
            presupuesto_personal_pax_dia REAL,
            presupuesto_limpieza_lenceria_pax_dia REAL,
            presupuesto_suministros_pax_dia REAL,
            presupuesto_mantenimiento_pax_dia REAL,
            presupuesto_alojamiento_estructura_pax_dia REAL,
            margen_objetivo_pax_dia REAL,
            fuente TEXT,
            observaciones TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, centro_id, year)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS imserso_economia_diaria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER,
            centro_id INTEGER,
            year INTEGER,
            fecha TEXT,
            pax_imserso INTEGER DEFAULT 0,
            habitaciones_imserso INTEGER DEFAULT 0,
            ingreso_imserso REAL DEFAULT 0,
            presupuesto_comida REAL DEFAULT 0,
            presupuesto_personal REAL DEFAULT 0,
            presupuesto_limpieza_lenceria REAL DEFAULT 0,
            presupuesto_suministros REAL DEFAULT 0,
            presupuesto_mantenimiento REAL DEFAULT 0,
            presupuesto_alojamiento_estructura REAL DEFAULT 0,
            margen_objetivo REAL DEFAULT 0,
            coste_equilibrio_estimado REAL DEFAULT 0,
            brecha_equilibrio REAL DEFAULT 0,
            lectura TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, centro_id, year, fecha)
        )
        """
    )


def ensure_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    column = definition.split()[0]
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def upsert_config(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int, importe: float, equilibrio: float, suplemento_individual: float) -> dict:
    ensure_column(conn, "imserso_contrato_config", "operador TEXT DEFAULT 'Mundiplan'")
    ensure_column(conn, "imserso_contrato_config", "suplemento_individual_dia REAL DEFAULT 0")
    cfg = dict(DEFAULTS)
    cfg["importe_pax_dia"] = importe
    cfg["suplemento_individual_dia"] = suplemento_individual
    cfg["punto_equilibrio_pax_dia"] = equilibrio
    conn.execute(
        """
        INSERT INTO imserso_contrato_config
        (empresa_id, centro_id, year, operador, importe_pax_dia, suplemento_individual_dia, punto_equilibrio_pax_dia,
         presupuesto_comida_pax_dia, presupuesto_personal_pax_dia,
         presupuesto_limpieza_lenceria_pax_dia, presupuesto_suministros_pax_dia,
         presupuesto_mantenimiento_pax_dia, presupuesto_alojamiento_estructura_pax_dia,
         margen_objetivo_pax_dia, fuente, observaciones)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(empresa_id, centro_id, year)
        DO UPDATE SET operador=excluded.operador,
            importe_pax_dia=excluded.importe_pax_dia,
            suplemento_individual_dia=excluded.suplemento_individual_dia,
            punto_equilibrio_pax_dia=excluded.punto_equilibrio_pax_dia,
            presupuesto_comida_pax_dia=excluded.presupuesto_comida_pax_dia,
            presupuesto_personal_pax_dia=excluded.presupuesto_personal_pax_dia,
            presupuesto_limpieza_lenceria_pax_dia=excluded.presupuesto_limpieza_lenceria_pax_dia,
            presupuesto_suministros_pax_dia=excluded.presupuesto_suministros_pax_dia,
            presupuesto_mantenimiento_pax_dia=excluded.presupuesto_mantenimiento_pax_dia,
            presupuesto_alojamiento_estructura_pax_dia=excluded.presupuesto_alojamiento_estructura_pax_dia,
            margen_objetivo_pax_dia=excluded.margen_objetivo_pax_dia,
            fuente=excluded.fuente,
            observaciones=excluded.observaciones,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            empresa_id,
            centro_id,
            year,
            cfg["operador"],
            cfg["importe_pax_dia"],
            cfg["suplemento_individual_dia"],
            cfg["punto_equilibrio_pax_dia"],
            cfg["presupuesto_comida_pax_dia"],
            cfg["presupuesto_personal_pax_dia"],
            cfg["presupuesto_limpieza_lenceria_pax_dia"],
            cfg["presupuesto_suministros_pax_dia"],
            cfg["presupuesto_mantenimiento_pax_dia"],
            cfg["presupuesto_alojamiento_estructura_pax_dia"],
            cfg["margen_objetivo_pax_dia"],
            "Contrato informado por usuario: Mundiplan 45 EUR pax/dia pension completa con agua y vino de la casa.",
            f"Suplemento habitacion individual: {cfg['suplemento_individual_dia']:.2f} EUR/dia. Configurable; no sustituye liquidacion oficial.",
        ),
    )
    return cfg


def update_imserso_reservations(conn: sqlite3.Connection, empresa_id: int, centro_id: int, importe_pax_dia: float) -> int:
    count = 0
    rows = conn.execute(
        """
        SELECT id, fecha_entrada, fecha_salida, adultos
        FROM reservas_recepcion
        WHERE empresa_id=? AND centro_id=? AND origen=?
          AND observaciones_operativas LIKE '%Segmento inserso%'
        """,
        (empresa_id, centro_id, ORIGEN),
    ).fetchall()
    for row in rows:
        nights = max((date.fromisoformat(row["fecha_salida"]) - date.fromisoformat(row["fecha_entrada"])).days, 1)
        pax = int(row["adultos"] or 0)
        total = round(pax * nights * importe_pax_dia, 2)
        conn.execute(
            """
            UPDATE reservas_recepcion
            SET precio_total=?, deposito=0, saldo=?, updated_at=CURRENT_TIMESTAMP,
                observaciones=TRIM(COALESCE(observaciones,'') || ' | Imserso recalculado a importe pax/dia configurable.')
            WHERE id=?
            """,
            (total, total, row["id"]),
        )
        count += 1
    return count


def rebuild_daily(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int, cfg: dict) -> int:
    conn.execute(
        "DELETE FROM imserso_economia_diaria WHERE empresa_id=? AND centro_id=? AND year=?",
        (empresa_id, centro_id, year),
    )
    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)
    inserted = 0
    for day in daterange(start, end):
        rows = conn.execute(
            """
            SELECT adultos
            FROM reservas_recepcion
            WHERE empresa_id=? AND centro_id=? AND origen=?
              AND observaciones_operativas LIKE '%Segmento inserso%'
              AND fecha_entrada <= ? AND fecha_salida > ?
              AND estado NOT IN ('cancelada','no_show')
            """,
            (empresa_id, centro_id, ORIGEN, day.isoformat(), day.isoformat()),
        ).fetchall()
        if not rows:
            continue
        rooms = len(rows)
        pax = sum(int(r["adultos"] or 0) for r in rows)
        ingreso = round(pax * cfg["importe_pax_dia"], 2)
        comida = round(pax * cfg["presupuesto_comida_pax_dia"], 2)
        personal = round(pax * cfg["presupuesto_personal_pax_dia"], 2)
        limpieza = round(pax * cfg["presupuesto_limpieza_lenceria_pax_dia"], 2)
        suministros = round(pax * cfg["presupuesto_suministros_pax_dia"], 2)
        mantenimiento = round(pax * cfg["presupuesto_mantenimiento_pax_dia"], 2)
        alojamiento = round(pax * cfg["presupuesto_alojamiento_estructura_pax_dia"], 2)
        margen = round(pax * cfg["margen_objetivo_pax_dia"], 2)
        equilibrio = round(pax * cfg["punto_equilibrio_pax_dia"], 2)
        brecha = round(ingreso - equilibrio, 2)
        lectura = "Riesgo: ingreso Imserso por debajo del punto de equilibrio estimado." if brecha < 0 else "Ingreso Imserso cubre punto de equilibrio estimado."
        conn.execute(
            """
            INSERT INTO imserso_economia_diaria
            (empresa_id, centro_id, year, fecha, pax_imserso, habitaciones_imserso,
             ingreso_imserso, presupuesto_comida, presupuesto_personal,
             presupuesto_limpieza_lenceria, presupuesto_suministros,
             presupuesto_mantenimiento, presupuesto_alojamiento_estructura,
             margen_objetivo, coste_equilibrio_estimado, brecha_equilibrio, lectura)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                empresa_id,
                centro_id,
                year,
                day.isoformat(),
                pax,
                rooms,
                ingreso,
                comida,
                personal,
                limpieza,
                suministros,
                mantenimiento,
                alojamiento,
                margen,
                equilibrio,
                brecha,
                lectura,
            ),
        )
        inserted += 1
    return inserted


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        cfg = upsert_config(conn, args.empresa_id, args.centro_id, args.year, args.importe_pax_dia, args.punto_equilibrio_pax_dia, args.suplemento_individual_dia)
        updated = update_imserso_reservations(conn, args.empresa_id, args.centro_id, cfg["importe_pax_dia"])
        days = rebuild_daily(conn, args.empresa_id, args.centro_id, args.year, cfg)
        conn.commit()
        total = conn.execute(
            """
            SELECT ROUND(SUM(ingreso_imserso),2), ROUND(SUM(presupuesto_comida),2),
                   ROUND(SUM(coste_equilibrio_estimado),2), ROUND(SUM(brecha_equilibrio),2)
            FROM imserso_economia_diaria
            WHERE empresa_id=? AND centro_id=? AND year=?
            """,
            (args.empresa_id, args.centro_id, args.year),
        ).fetchone()
        print({
            "reservas_imserso_actualizadas": updated,
            "dias_imserso": days,
            "ingreso_imserso": total[0] or 0,
            "presupuesto_comida": total[1] or 0,
            "coste_equilibrio": total[2] or 0,
            "brecha_equilibrio": total[3] or 0,
        })
    finally:
        conn.close()


if __name__ == "__main__":
    main()

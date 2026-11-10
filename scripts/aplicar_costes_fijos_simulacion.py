# -*- coding: utf-8 -*-
"""Aplica costes fijos de estructura a la simulacion existente.

No borra reservas ni operativa. Anade luz, agua, seguros, IBI, tasas y otros
gastos estructurales a los KPIs diarios y crea resumen mensual verificable.
"""

from __future__ import annotations

import argparse
import calendar
import sqlite3
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3

GASTOS_FIJOS_MENSUALES_HOTEL1 = [
    ("electricidad", "Electricidad y energia", 18500),
    ("agua", "Agua, saneamiento y piscina", 4800),
    ("seguros", "Seguros multirriesgo y RC", 3200),
    ("ibi", "IBI y tributos municipales", 6100),
    ("tasas", "Basuras, terrazas, licencias y tasas", 2100),
    ("telecom", "Telefonia, internet, TV y sistemas", 1800),
    ("mantenimiento_contratado", "Contratos mantenimiento, ascensores y climatizacion", 9400),
    ("asesoria", "Asesoria, legal, laboral y fiscal", 2600),
    ("software", "Software, PMS, channel manager y pasarelas", 2400),
    ("seguridad", "Seguridad, PRL y vigilancia tecnica", 1900),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aplicar costes fijos a KPIs de simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_column_if_missing(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def dinero(value: float) -> float:
    return round(float(value or 0), 2)


def latest_corrida(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    add_column_if_missing(conn, "simulador_vida_kpis_diarios", "coste_fijo_estimado REAL DEFAULT 0")
    add_column_if_missing(conn, "simulador_vida_kpis_diarios", "coste_total_estimado REAL DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_costes_fijos_mensuales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            mes TEXT,
            concepto TEXT,
            descripcion TEXT,
            importe_mensual REAL DEFAULT 0,
            tipo TEXT DEFAULT 'estructura',
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes, concepto)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_resultado_mensual (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            mes TEXT,
            dias_calendario INTEGER DEFAULT 0,
            dias_simulados INTEGER DEFAULT 0,
            dias_negativos INTEGER DEFAULT 0,
            ingresos_total REAL DEFAULT 0,
            coste_laboral REAL DEFAULT 0,
            coste_compras REAL DEFAULT 0,
            coste_fijo REAL DEFAULT 0,
            coste_total REAL DEFAULT 0,
            margen REAL DEFAULT 0,
            precio_medio_habitacion REAL DEFAULT 0,
            punto_muerto_diario REAL DEFAULT 0,
            lectura TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(corrida_id, centro_id, mes)
        )
        """
    )


def month_iter(start: date, end: date):
    actual = date(start.year, start.month, 1)
    while actual <= end:
        yield actual
        actual = date(actual.year + 1, 1, 1) if actual.month == 12 else date(actual.year, actual.month + 1, 1)


def seed_costes(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date) -> None:
    for mes_dt in month_iter(start, end):
        mes = mes_dt.strftime("%Y-%m")
        for concepto, descripcion, importe in GASTOS_FIJOS_MENSUALES_HOTEL1:
            factor = 1.0
            if mes_dt.month in {7, 8} and concepto in {"electricidad", "agua", "mantenimiento_contratado"}:
                factor = 1.18
            elif mes_dt.month in {11, 12, 1, 2} and concepto in {"electricidad", "mantenimiento_contratado"}:
                factor = 0.82
            conn.execute(
                """
                INSERT INTO simulador_costes_fijos_mensuales
                (corrida_id, centro_id, mes, concepto, descripcion, importe_mensual, tipo, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, 'estructura', 1, ?)
                ON CONFLICT(corrida_id, centro_id, mes, concepto) DO UPDATE SET
                    descripcion=excluded.descripcion,
                    importe_mensual=excluded.importe_mensual,
                    activo=1
                """,
                (corrida_id, centro_id, mes, concepto, descripcion, dinero(importe * factor), EMPRESA_ID),
            )


def coste_fijo_dia(conn: sqlite3.Connection, corrida_id: int, centro_id: int, fecha: str) -> float:
    day = date.fromisoformat(fecha)
    mes = fecha[:7]
    total = conn.execute(
        """
        SELECT COALESCE(SUM(importe_mensual), 0) total
        FROM simulador_costes_fijos_mensuales
        WHERE corrida_id=? AND centro_id=? AND mes=? AND COALESCE(activo,1)=1
        """,
        (corrida_id, centro_id, mes),
    ).fetchone()["total"]
    return dinero(float(total or 0) / calendar.monthrange(day.year, day.month)[1])


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
            raise SystemExit("No hay corrida de simulacion.")
        rango = conn.execute(
            "SELECT MIN(fecha) inicio, MAX(fecha) fin FROM simulador_vida_kpis_diarios WHERE corrida_id=? AND centro_id=?",
            (corrida_id, CENTRO_ID),
        ).fetchone()
        if not rango["inicio"]:
            raise SystemExit("No hay KPIs diarios para la corrida.")
        seed_costes(conn, corrida_id, CENTRO_ID, date.fromisoformat(rango["inicio"]), date.fromisoformat(rango["fin"]))
        actualizados = 0
        for row in conn.execute(
            "SELECT * FROM simulador_vida_kpis_diarios WHERE corrida_id=? AND centro_id=? ORDER BY fecha",
            (corrida_id, CENTRO_ID),
        ).fetchall():
            coste_fijo = coste_fijo_dia(conn, corrida_id, CENTRO_ID, row["fecha"])
            coste_total = dinero(float(row["coste_laboral_estimado"] or 0) + float(row["coste_compras_estimado"] or 0) + coste_fijo)
            ingresos = dinero(float(row["ingresos_habitaciones"] or 0) + float(row["ingresos_tpv"] or 0))
            margen = dinero(ingresos - coste_total)
            lectura = row["lectura"] or ""
            if margen < 0 and "costes fijos" not in lectura.lower():
                lectura = "Dia con margen negativo estimado incluyendo costes fijos: revisar precio, compras, plantilla, suministros y mix de clientes."
            conn.execute(
                """
                UPDATE simulador_vida_kpis_diarios
                SET coste_fijo_estimado=?, coste_total_estimado=?, margen_estimado=?, lectura=?
                WHERE id=?
                """,
                (coste_fijo, coste_total, margen, lectura, row["id"]),
            )
            actualizados += 1
        for row in conn.execute(
            """
            SELECT substr(fecha,1,7) mes,
                   COUNT(DISTINCT fecha) dias_simulados,
                   SUM(CASE WHEN margen_estimado < 0 THEN 1 ELSE 0 END) dias_negativos,
                   SUM(ingresos_habitaciones + ingresos_tpv) ingresos_total,
                   SUM(coste_laboral_estimado) coste_laboral,
                   SUM(coste_compras_estimado) coste_compras,
                   SUM(coste_fijo_estimado) coste_fijo,
                   SUM(coste_total_estimado) coste_total,
                   SUM(margen_estimado) margen,
                   SUM(habitaciones_ocupadas) habitaciones_vendidas
            FROM simulador_vida_kpis_diarios
            WHERE corrida_id=? AND centro_id=?
            GROUP BY substr(fecha,1,7)
            ORDER BY mes
            """,
            (corrida_id, CENTRO_ID),
        ).fetchall():
            year, month = map(int, row["mes"].split("-"))
            dias_calendario = calendar.monthrange(year, month)[1]
            habitaciones = float(row["habitaciones_vendidas"] or 0)
            precio_medio = dinero(float(row["ingresos_total"] or 0) / habitaciones) if habitaciones else 0
            punto_muerto = dinero(float(row["coste_total"] or 0) / max(int(row["dias_simulados"] or 1), 1))
            lectura = "Mes rentable." if float(row["margen"] or 0) >= 0 else "Mes en negativo: subir ingreso neto o redimensionar estructura."
            conn.execute(
                """
                INSERT INTO simulador_resultado_mensual
                (corrida_id, centro_id, mes, dias_calendario, dias_simulados, dias_negativos,
                 ingresos_total, coste_laboral, coste_compras, coste_fijo, coste_total,
                 margen, precio_medio_habitacion, punto_muerto_diario, lectura, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(corrida_id, centro_id, mes) DO UPDATE SET
                    dias_calendario=excluded.dias_calendario,
                    dias_simulados=excluded.dias_simulados,
                    dias_negativos=excluded.dias_negativos,
                    ingresos_total=excluded.ingresos_total,
                    coste_laboral=excluded.coste_laboral,
                    coste_compras=excluded.coste_compras,
                    coste_fijo=excluded.coste_fijo,
                    coste_total=excluded.coste_total,
                    margen=excluded.margen,
                    precio_medio_habitacion=excluded.precio_medio_habitacion,
                    punto_muerto_diario=excluded.punto_muerto_diario,
                    lectura=excluded.lectura,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    corrida_id,
                    CENTRO_ID,
                    row["mes"],
                    dias_calendario,
                    int(row["dias_simulados"] or 0),
                    int(row["dias_negativos"] or 0),
                    dinero(row["ingresos_total"] or 0),
                    dinero(row["coste_laboral"] or 0),
                    dinero(row["coste_compras"] or 0),
                    dinero(row["coste_fijo"] or 0),
                    dinero(row["coste_total"] or 0),
                    dinero(row["margen"] or 0),
                    precio_medio,
                    punto_muerto,
                    lectura,
                    EMPRESA_ID,
                ),
            )
        conn.commit()
        agosto = conn.execute(
            "SELECT * FROM simulador_resultado_mensual WHERE corrida_id=? AND centro_id=? AND mes='2026-08'",
            (corrida_id, CENTRO_ID),
        ).fetchone()
        extra = ""
        if agosto:
            extra = f" Agosto: {agosto['dias_negativos']} negativos / {agosto['dias_calendario']} dias calendario."
        print(f"Costes fijos aplicados: {actualizados} dias actualizados.{extra}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Genera presupuesto contable de laboratorio desde KPIs diarios simulados.

Uso pensado para Hotel 1 laboratorio. No crea operativa nueva: solo resume los
KPIs diarios ya existentes en contabilidad_presupuestos para que los informes,
la IA y la prueba de fuego no lean presupuesto cero.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crear presupuesto de laboratorio desde KPIs diarios.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=3)
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--reset", action="store_true")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contabilidad_presupuestos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            anio INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            estado TEXT DEFAULT 'borrador',
            origen TEXT DEFAULT 'manual',
            observaciones TEXT,
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
            actualizado_en DATETIME,
            UNIQUE(empresa_id, centro_id, anio, nombre)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contabilidad_presupuesto_lineas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            presupuesto_id INTEGER NOT NULL,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            anio INTEGER NOT NULL,
            mes INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            bloque TEXT NOT NULL,
            concepto TEXT NOT NULL,
            importe REAL DEFAULT 0,
            cuenta_contable TEXT,
            fuente TEXT,
            fiabilidad TEXT DEFAULT 'estimado',
            observaciones TEXT,
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (presupuesto_id) REFERENCES contabilidad_presupuestos(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_conta_presupuesto_lineas
        ON contabilidad_presupuesto_lineas(empresa_id, centro_id, anio, mes, tipo, bloque)
        """
    )


def latest_corrida(conn: sqlite3.Connection, centro_id: int) -> int:
    if not table_exists(conn, "simulador_vida_corridas"):
        return 0
    row = conn.execute(
        "SELECT id FROM simulador_vida_corridas WHERE centro_id=? ORDER BY id DESC LIMIT 1",
        (centro_id,),
    ).fetchone()
    return int(row["id"]) if row else 0


def get_or_create_presupuesto(conn: sqlite3.Connection, empresa_id: int, centro_id: int, anio: int) -> int:
    nombre = f"Presupuesto laboratorio Hotel 1 {anio}"
    row = conn.execute(
        """
        SELECT id FROM contabilidad_presupuestos
        WHERE empresa_id=? AND centro_id=? AND anio=? AND nombre=?
        """,
        (empresa_id, centro_id, anio, nombre),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE contabilidad_presupuestos
            SET estado='simulado', origen='simulador_kpis_diarios',
                observaciones='Presupuesto de laboratorio agregado desde KPIs diarios simulados.',
                actualizado_en=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (row["id"],),
        )
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO contabilidad_presupuestos
        (empresa_id, centro_id, anio, nombre, estado, origen, observaciones)
        VALUES (?, ?, ?, ?, 'simulado', 'simulador_kpis_diarios',
                'Presupuesto de laboratorio agregado desde KPIs diarios simulados.')
        """,
        (empresa_id, centro_id, anio, nombre),
    )
    return int(cur.lastrowid)


def add_line(
    conn: sqlite3.Connection,
    presupuesto_id: int,
    empresa_id: int,
    centro_id: int,
    anio: int,
    mes: int,
    tipo: str,
    bloque: str,
    concepto: str,
    importe: float,
    cuenta: str,
) -> None:
    if abs(float(importe or 0)) < 0.005:
        return
    conn.execute(
        """
        INSERT INTO contabilidad_presupuesto_lineas
        (presupuesto_id, empresa_id, centro_id, anio, mes, tipo, bloque, concepto,
         importe, cuenta_contable, fuente, fiabilidad, observaciones)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'simulador_vida_kpis_diarios',
                'simulado', 'Linea agregada desde KPIs diarios del laboratorio.')
        """,
        (presupuesto_id, empresa_id, centro_id, anio, mes, tipo, bloque, concepto, round(float(importe or 0), 2), cuenta),
    )


def generar(conn: sqlite3.Connection, empresa_id: int, centro_id: int, corrida_id: int, reset: bool) -> dict:
    if not table_exists(conn, "simulador_vida_kpis_diarios"):
        raise SystemExit("No existe simulador_vida_kpis_diarios.")
    rows = conn.execute(
        """
        SELECT CAST(substr(fecha,1,4) AS INTEGER) anio,
               CAST(substr(fecha,6,2) AS INTEGER) mes,
               ROUND(SUM(COALESCE(ingresos_habitaciones,0)),2) ingresos_habitaciones,
               ROUND(SUM(COALESCE(ingresos_tpv,0)),2) ingresos_tpv,
               ROUND(SUM(COALESCE(coste_laboral_estimado,0)),2) coste_laboral,
               ROUND(SUM(COALESCE(coste_compras_estimado,0)),2) coste_compras,
               ROUND(SUM(COALESCE(coste_fijo_estimado,0)),2) coste_fijo,
               ROUND(SUM(COALESCE(coste_total_estimado,0)),2) coste_total,
               ROUND(SUM(COALESCE(margen_estimado,0)),2) margen
        FROM simulador_vida_kpis_diarios
        WHERE corrida_id=? AND empresa_id=? AND centro_id=?
        GROUP BY substr(fecha,1,7)
        ORDER BY anio, mes
        """,
        (corrida_id, empresa_id, centro_id),
    ).fetchall()
    if not rows:
        raise SystemExit("No hay KPIs para esa corrida/centro.")

    presupuestos: dict[int, int] = {}
    lineas = 0
    for row in rows:
        anio = int(row["anio"])
        mes = int(row["mes"])
        presupuesto_id = presupuestos.get(anio)
        if not presupuesto_id:
            presupuesto_id = get_or_create_presupuesto(conn, empresa_id, centro_id, anio)
            presupuestos[anio] = presupuesto_id
            if reset:
                conn.execute("DELETE FROM contabilidad_presupuesto_lineas WHERE presupuesto_id=?", (presupuesto_id,))
        add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "ingreso", "Alojamiento", "Habitaciones simuladas", row["ingresos_habitaciones"], "705")
        add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "ingreso", "TPV", "Consumos TPV simulados", row["ingresos_tpv"], "705")
        add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Personal", "Coste laboral estimado", row["coste_laboral"], "640")
        add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Comida y bebida", "Compras consumidas estimadas", row["coste_compras"], "600")
        add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Costes fijos", "Estructura y suministros estimados", row["coste_fijo"], "629")
        lineas += 5
    return {"presupuestos": len(presupuestos), "lineas": lineas, "corrida_id": corrida_id}


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        corrida_id = args.corrida_id or latest_corrida(conn, args.centro_id)
        if not corrida_id:
            raise SystemExit("No hay corrida para el centro indicado.")
        resultado = generar(conn, args.empresa_id, args.centro_id, corrida_id, args.reset)
        conn.commit()
        print(resultado)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

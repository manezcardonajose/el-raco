# -*- coding: utf-8 -*-
"""Calcula escandallos diarios y costes de produccion por plato.

Usa ingredientes reales si existen; si no, calcula el coste teorico desde la
receta. Tambien actualiza lineas_produccion con coste_estimado/coste_real.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generar escandallos diarios de produccion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def latest_corrida(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "simulador_vida_corridas"):
        return 0
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def safe_add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.strip().split()[0]
    if name not in columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    if table_exists(conn, "recetas"):
        safe_add_column(conn, "recetas", "escandallo_fiable INTEGER DEFAULT 1")
        safe_add_column(conn, "recetas", "origen_escandallo TEXT")
        safe_add_column(conn, "recetas", "coste_validado INTEGER DEFAULT 0")
    if table_exists(conn, "lineas_produccion"):
        safe_add_column(conn, "lineas_produccion", "coste_estimado REAL")
        safe_add_column(conn, "lineas_produccion", "coste_real REAL")
        safe_add_column(conn, "lineas_produccion", "coste_unitario_teorico REAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_escandallos_diarios_plato (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            fecha TEXT NOT NULL,
            servicio TEXT,
            parte_id INTEGER,
            linea_produccion_id INTEGER NOT NULL,
            receta_id INTEGER,
            plato TEXT,
            categoria TEXT,
            estacion_buffet TEXT,
            raciones_planificadas REAL DEFAULT 0,
            raciones_reales REAL DEFAULT 0,
            ingredientes_teoricos INTEGER DEFAULT 0,
            ingredientes_reales INTEGER DEFAULT 0,
            coste_teorico REAL DEFAULT 0,
            coste_real REAL DEFAULT 0,
            desviacion_coste REAL DEFAULT 0,
            desviacion_pct REAL DEFAULT 0,
            coste_racion_teorico REAL DEFAULT 0,
            coste_racion_real REAL DEFAULT 0,
            sobrante_raciones REAL DEFAULT 0,
            coste_merma_estimado REAL DEFAULT 0,
            coste_reaprovechado_estimado REAL DEFAULT 0,
            destino_sobrante TEXT,
            temperatura_fin REAL,
            estado_linea TEXT,
            riesgo TEXT,
            lectura TEXT,
            accion TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, linea_produccion_id)
        )
        """
    )
    safe_add_column(conn, "simulador_escandallos_diarios_plato", "coste_reaprovechado_estimado REAL DEFAULT 0")
    safe_add_column(conn, "simulador_escandallos_diarios_plato", "destino_sobrante TEXT")


def risk_and_action(
    coste_teorico: float,
    coste_real: float,
    desviacion_pct: float,
    coste_racion_real: float,
    sobrante: float,
    raciones: float,
    coste_merma: float,
    temperatura: float | None,
    estado_linea: str,
) -> tuple[str, str, str]:
    problemas: list[str] = []
    if coste_real > 0 and coste_teorico <= 0:
        problemas.append("sin escandallo teorico fiable")
    if desviacion_pct > 20:
        problemas.append("desviacion de coste alta")
    elif desviacion_pct > 8:
        problemas.append("desviacion de coste media")
    if coste_racion_real > 8:
        problemas.append("coste por racion alto")
    sobrante_pct = (sobrante / max(raciones, 1)) if raciones else 0
    if raciones and sobrante_pct > 0.14:
        problemas.append("sobrante elevado")
    if coste_merma > 80:
        problemas.append("merma valorada alta")
    elif coste_merma > 25:
        problemas.append("merma valorada media")
    if temperatura is not None and (temperatura < 1 or temperatura > 75):
        problemas.append("temperatura a revisar")
    if estado_linea == "incidencia":
        problemas.append("incidencia de produccion")

    if any(p in problemas for p in ("desviacion de coste alta", "sin escandallo teorico fiable", "incidencia de produccion", "merma valorada alta")):
        riesgo = "alto"
    elif problemas:
        riesgo = "medio"
    else:
        riesgo = "bajo"

    if not problemas:
        lectura = "Coste y produccion dentro de rango."
        accion = "Mantener receta, racionado y compra."
    elif "merma valorada alta" in problemas or "sobrante elevado" in problemas:
        lectura = "Revisar: " + ", ".join(problemas) + "."
        accion = "Reducir produccion prevista, ajustar reposicion por tandas y decidir sobrante: reaprovechamiento APPCC, guardado trazado o merma con motivo."
    else:
        lectura = "Revisar: " + ", ".join(problemas) + "."
        accion = "Comparar ficha tecnica, cantidad real, merma, precio proveedor y racionado antes del siguiente servicio."
    return riesgo, lectura, accion


def generar(conn: sqlite3.Connection, corrida_id: int, empresa_id: int, centro_id: int) -> dict:
    ensure_schema(conn)
    required = {"partes_produccion", "lineas_produccion", "recetas", "recetas_ingredientes", "productos"}
    if not all(table_exists(conn, table) for table in required):
        return {"platos": 0, "omitido": "Faltan tablas de produccion o recetas."}

    conn.execute(
        "DELETE FROM simulador_escandallos_diarios_plato WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )

    rows = conn.execute(
        """
        WITH base AS (
            SELECT pp.id AS parte_id, pp.fecha, COALESCE(pp.turno, 'Sin servicio') servicio,
                   lp.id AS linea_id, lp.receta_id,
                   r.nombre AS plato, r.categoria, COALESCE(r.escandallo_fiable, 1) AS escandallo_fiable,
                   lp.estacion_buffet,
                   COALESCE(lp.raciones_planificadas, lp.cantidad_planificada, 0) AS raciones_planificadas,
                   COALESCE(lp.cantidad_real, lp.cantidad_producida, lp.raciones_planificadas, lp.cantidad_planificada, 0) AS raciones_reales,
                   COALESCE(lp.estado, 'pendiente') AS estado_linea,
                   pc.cantidad_sobrante,
                   pc.destino_sobrante,
                   pc.temperatura_fin_elaboracion
            FROM partes_produccion pp
            JOIN lineas_produccion lp ON lp.parte_id=pp.id
            JOIN recetas r ON r.id=lp.receta_id
            LEFT JOIN produccion_controles_appcc pc ON pc.linea_produccion_id=lp.id
            WHERE pp.empresa_id=? AND pp.centro_id=?
        ),
        teorico AS (
            SELECT b.linea_id,
                   COUNT(ri.id) ingredientes,
                   CASE
                       WHEN MAX(COALESCE(b.escandallo_fiable, 1)) = 1
                       THEN SUM(COALESCE(ri.cantidad, 0) * COALESCE(b.raciones_reales, 0) * COALESCE(p.precio, 0))
                       ELSE 0
                   END AS coste
            FROM base b
            LEFT JOIN recetas_ingredientes ri ON ri.receta_id=b.receta_id
            LEFT JOIN productos p ON p.id=ri.producto_id
            GROUP BY b.linea_id
        ),
        real AS (
            SELECT b.linea_id,
                   COUNT(pir.id) ingredientes,
                   SUM(COALESCE(pir.cantidad_real, 0) * COALESCE(p.precio, 0)) coste
            FROM base b
            JOIN produccion_ingredientes_reales pir ON pir.linea_produccion_id=b.linea_id
            JOIN productos p ON p.id=pir.producto_id
            WHERE COALESCE(pir.cantidad_real, 0) > 0
            GROUP BY b.linea_id
        )
        SELECT b.*, COALESCE(t.ingredientes, 0) ingredientes_teoricos,
               COALESCE(t.coste, 0) coste_teorico,
               COALESCE(rr.ingredientes, 0) ingredientes_reales,
               CASE WHEN COALESCE(rr.ingredientes, 0) > 0 THEN COALESCE(rr.coste, 0)
                    ELSE COALESCE(t.coste, 0) END AS coste_real
        FROM base b
        LEFT JOIN teorico t ON t.linea_id=b.linea_id
        LEFT JOIN real rr ON rr.linea_id=b.linea_id
        ORDER BY b.fecha, b.servicio, b.linea_id
        """,
        (empresa_id, centro_id),
    ).fetchall()

    inserted = 0
    for row in rows:
        coste_teorico = round(float(row["coste_teorico"] or 0), 2)
        coste_real = round(float(row["coste_real"] or 0), 2)
        raciones_plan = float(row["raciones_planificadas"] or 0)
        raciones_real = float(row["raciones_reales"] or 0)
        desviacion = round(coste_real - coste_teorico, 2)
        desviacion_pct = round((desviacion / coste_teorico * 100), 1) if coste_teorico else (100.0 if coste_real else 0.0)
        coste_racion_teorico = round(coste_teorico / max(raciones_plan, 1), 2)
        coste_racion_real = round(coste_real / max(raciones_real, 1), 2)
        sobrante = float(row["cantidad_sobrante"] or 0)
        destino_sobrante = str(row["destino_sobrante"] or "").strip().lower()
        coste_sobrante = round(coste_racion_real * sobrante, 2)
        coste_reaprovechado = coste_sobrante if destino_sobrante in {"reaprovechamiento", "guardado_appcc", "guardado"} else 0.0
        coste_merma = 0.0 if coste_reaprovechado else coste_sobrante
        temperatura = row["temperatura_fin_elaboracion"]
        temperatura = None if temperatura is None else float(temperatura)
        riesgo, lectura, accion = risk_and_action(
            coste_teorico,
            coste_real,
            desviacion_pct,
            coste_racion_real,
            sobrante,
            raciones_real,
            coste_merma,
            temperatura,
            str(row["estado_linea"] or ""),
        )
        conn.execute(
            """
            UPDATE lineas_produccion
            SET coste_estimado=?, coste_real=?, coste_unitario_teorico=?
            WHERE id=?
            """,
            (coste_teorico, coste_real, coste_racion_teorico, row["linea_id"]),
        )
        conn.execute(
            """
            INSERT INTO simulador_escandallos_diarios_plato
            (corrida_id, centro_id, fecha, servicio, parte_id, linea_produccion_id,
             receta_id, plato, categoria, estacion_buffet, raciones_planificadas,
             raciones_reales, ingredientes_teoricos, ingredientes_reales,
             coste_teorico, coste_real, desviacion_coste, desviacion_pct,
             coste_racion_teorico, coste_racion_real, sobrante_raciones,
             coste_merma_estimado, coste_reaprovechado_estimado, destino_sobrante,
             temperatura_fin, estado_linea, riesgo,
             lectura, accion, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                centro_id,
                row["fecha"],
                row["servicio"],
                row["parte_id"],
                row["linea_id"],
                row["receta_id"],
                row["plato"],
                row["categoria"],
                row["estacion_buffet"],
                round(raciones_plan, 2),
                round(raciones_real, 2),
                row["ingredientes_teoricos"],
                row["ingredientes_reales"],
                coste_teorico,
                coste_real,
                desviacion,
                desviacion_pct,
                coste_racion_teorico,
                coste_racion_real,
                round(sobrante, 2),
                coste_merma,
                coste_reaprovechado,
                destino_sobrante or None,
                temperatura,
                row["estado_linea"],
                riesgo,
                lectura,
                accion,
                empresa_id,
            ),
        )
        inserted += 1

    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) platos,
               COUNT(DISTINCT fecha) dias,
               ROUND(SUM(coste_teorico), 2) coste_teorico,
               ROUND(SUM(coste_real), 2) coste_real,
               ROUND(SUM(desviacion_coste), 2) desviacion,
               ROUND(SUM(coste_merma_estimado), 2) coste_merma,
               ROUND(SUM(coste_reaprovechado_estimado), 2) coste_reaprovechado,
               SUM(CASE WHEN riesgo='alto' THEN 1 ELSE 0 END) altos,
               SUM(CASE WHEN riesgo='medio' THEN 1 ELSE 0 END) medios
        FROM simulador_escandallos_diarios_plato
        WHERE corrida_id=? AND centro_id=?
        """,
        (corrida_id, centro_id),
    ).fetchone()
    return dict(resumen) | {"insertados": inserted}


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida de simulacion.")
        print(generar(conn, corrida_id, args.empresa_id, args.centro_id))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Calcula impacto mensual de aplicar recomendaciones de cocina."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generar impacto mensual de recomendaciones de cocina.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
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


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_impacto_cocina_mensual (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            mes TEXT NOT NULL,
            platos_intervenidos INTEGER DEFAULT 0,
            lineas_afectadas INTEGER DEFAULT 0,
            coste_afectado REAL DEFAULT 0,
            merma_afectada REAL DEFAULT 0,
            ahorro_estimado REAL DEFAULT 0,
            margen_antes REAL DEFAULT 0,
            margen_despues REAL DEFAULT 0,
            estado_antes TEXT,
            estado_despues TEXT,
            top_platos TEXT,
            decision TEXT,
            condicion_control TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes)
        )
        """
    )


def semaforo(margen: float, ingresos: float) -> str:
    objetivo = max(30000.0, ingresos * 0.06)
    if margen >= objetivo:
        return "verde"
    if margen >= 0:
        return "ambar"
    return "rojo"


def ahorro_expr() -> str:
    return """
        CASE r.recomendacion
            WHEN 'reducir_frecuencia_y_racion'
                THEN e.coste_merma_estimado * 0.35 + MAX(e.desviacion_coste, 0) * 0.20
            WHEN 'renegociar_o_sustituir_producto'
                THEN e.coste_real * 0.08 + e.coste_merma_estimado * 0.20
            WHEN 'producir_por_tandas'
                THEN e.coste_merma_estimado * 0.45
            WHEN 'revisar_appcc_y_proceso'
                THEN e.coste_merma_estimado * 0.20 + e.coste_real * 0.03
            ELSE e.coste_merma_estimado * 0.10
        END
    """


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    ensure_schema(conn)
    required = {
        "simulador_escandallos_diarios_plato",
        "simulador_recomendaciones_cocina_margen",
        "simulador_decision_financiera_mensual",
    }
    if not all(table_exists(conn, table) for table in required):
        return {"meses": 0, "omitido": "Faltan escandallos, recomendaciones o decision financiera."}

    conn.execute(
        "DELETE FROM simulador_impacto_cocina_mensual WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )

    rows = conn.execute(
        f"""
        WITH impacto_linea AS (
            SELECT substr(e.fecha, 1, 7) mes,
                   e.receta_id,
                   e.plato,
                   e.coste_real,
                   e.coste_merma_estimado,
                   {ahorro_expr()} AS ahorro
            FROM simulador_escandallos_diarios_plato e
            JOIN simulador_recomendaciones_cocina_margen r
              ON r.corrida_id=e.corrida_id
             AND r.centro_id=e.centro_id
             AND r.receta_id=e.receta_id
            WHERE e.corrida_id=? AND e.centro_id=?
        ),
        mensual AS (
            SELECT mes,
                   COUNT(*) lineas_afectadas,
                   COUNT(DISTINCT receta_id) platos_intervenidos,
                   ROUND(SUM(coste_real), 2) coste_afectado,
                   ROUND(SUM(coste_merma_estimado), 2) merma_afectada,
                   ROUND(SUM(ahorro), 2) ahorro_estimado
            FROM impacto_linea
            GROUP BY mes
        ),
        top AS (
            SELECT mes, GROUP_CONCAT(plato || ' (' || printf('%.0f', ahorro_plato) || ' EUR)', ', ') top_platos
            FROM (
                SELECT mes, plato, SUM(ahorro) ahorro_plato,
                       ROW_NUMBER() OVER (PARTITION BY mes ORDER BY SUM(ahorro) DESC) rn
                FROM impacto_linea
                GROUP BY mes, receta_id, plato
            )
            WHERE rn <= 3
            GROUP BY mes
        )
        SELECT m.*, t.top_platos, df.ingresos_total, df.margen_final_estimado, df.estado
        FROM mensual m
        LEFT JOIN top t ON t.mes=m.mes
        JOIN simulador_decision_financiera_mensual df
          ON df.corrida_id=? AND df.centro_id=? AND df.mes=m.mes
        ORDER BY m.mes
        """,
        (corrida_id, centro_id, corrida_id, centro_id),
    ).fetchall()

    inserted = 0
    for row in rows:
        margen_antes = float(row["margen_final_estimado"] or 0)
        ahorro = float(row["ahorro_estimado"] or 0)
        ingresos = float(row["ingresos_total"] or 0)
        margen_despues = margen_antes + ahorro
        estado_antes = row["estado"] or semaforo(margen_antes, ingresos)
        estado_despues = semaforo(margen_despues, ingresos)
        if estado_despues == "verde" and estado_antes != "verde":
            decision = "Cocina puede sacar el mes del riesgo si se ejecuta con disciplina."
        elif estado_despues == estado_antes:
            decision = "Cocina mejora margen, pero no basta: cruzar con ventas, RRHH y compras."
        else:
            decision = "Aplicar como plan de mejora y revisar semanalmente."
        condicion = (
            "Validar ahorro con consumo real, mermas al cierre, quejas de buffet, "
            "coste por racion y compras recibidas."
        )
        conn.execute(
            """
            INSERT INTO simulador_impacto_cocina_mensual
            (corrida_id, centro_id, mes, platos_intervenidos, lineas_afectadas,
             coste_afectado, merma_afectada, ahorro_estimado, margen_antes,
             margen_despues, estado_antes, estado_despues, top_platos,
             decision, condicion_control, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                centro_id,
                row["mes"],
                row["platos_intervenidos"],
                row["lineas_afectadas"],
                row["coste_afectado"],
                row["merma_afectada"],
                round(ahorro, 2),
                round(margen_antes, 2),
                round(margen_despues, 2),
                estado_antes,
                estado_despues,
                row["top_platos"],
                decision,
                condicion,
                EMPRESA_ID,
            ),
        )
        inserted += 1

    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) meses,
               ROUND(SUM(ahorro_estimado), 2) ahorro_estimado,
               SUM(CASE WHEN estado_antes!='verde' AND estado_despues='verde' THEN 1 ELSE 0 END) meses_rescatados,
               SUM(CASE WHEN estado_despues='rojo' THEN 1 ELSE 0 END) rojos_restantes,
               SUM(CASE WHEN estado_despues='ambar' THEN 1 ELSE 0 END) ambar_restantes
        FROM simulador_impacto_cocina_mensual
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
        print(generar(conn, corrida_id, args.centro_id))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

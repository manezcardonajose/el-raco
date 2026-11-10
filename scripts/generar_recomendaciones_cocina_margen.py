# -*- coding: utf-8 -*-
"""Propone acciones de cocina a partir de escandallos, coste y merma."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generar recomendaciones de cocina para proteger margen.")
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
        CREATE TABLE IF NOT EXISTS simulador_recomendaciones_cocina_margen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            receta_id INTEGER,
            plato TEXT,
            veces INTEGER DEFAULT 0,
            servicios TEXT,
            coste_total REAL DEFAULT 0,
            coste_racion REAL DEFAULT 0,
            merma_total REAL DEFAULT 0,
            sobrante_raciones REAL DEFAULT 0,
            desviacion_total REAL DEFAULT 0,
            desviacion_pct_media REAL DEFAULT 0,
            riesgo_alto INTEGER DEFAULT 0,
            prioridad TEXT,
            recomendacion TEXT,
            decision_propuesta TEXT,
            ahorro_estimado REAL DEFAULT 0,
            condicion_control TEXT,
            datos_json TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, receta_id, recomendacion)
        )
        """
    )


def classify(row: sqlite3.Row) -> tuple[str, str, str, float, str]:
    coste = float(row["coste_total"] or 0)
    merma = float(row["merma_total"] or 0)
    coste_racion = float(row["coste_racion"] or 0)
    altos = int(row["riesgo_alto"] or 0)
    veces = int(row["veces"] or 0)
    desviacion = float(row["desviacion_total"] or 0)

    if merma >= 40000 or coste >= 450000:
        prioridad = "critica"
    elif merma >= 12000 or coste >= 120000 or altos >= 40:
        prioridad = "alta"
    elif merma >= 5000 or coste_racion >= 6:
        prioridad = "media"
    else:
        prioridad = "baja"

    if merma >= 25000 and coste_racion >= 8:
        recomendacion = "reducir_frecuencia_y_racion"
        decision = "Probar menos apariciones en ciclo y racionado por tandas; no sacar toda la produccion al buffet."
        ahorro = merma * 0.35 + max(desviacion, 0) * 0.20
        condicion = "Medir quejas, reposicion y sobrante durante dos servicios antes de fijarlo en carta."
    elif coste_racion >= 10:
        recomendacion = "renegociar_o_sustituir_producto"
        decision = "Revisar proveedor, formato de compra y receta; si no baja el coste/racion, sustituir por alternativa equivalente."
        ahorro = coste * 0.08 + merma * 0.20
        condicion = "No bajar calidad percibida: comparar satisfaccion, consumo y coste/racion."
    elif merma >= 12000:
        recomendacion = "producir_por_tandas"
        decision = "Mantener plato, pero producir y reponer por tandas mas pequenas."
        ahorro = merma * 0.45
        condicion = "El jefe de partida valida reposicion y sobrante al cierre de cada servicio."
    elif altos >= 40:
        recomendacion = "revisar_appcc_y_proceso"
        decision = "Auditar temperatura, tiempos, mise en place y salida de camara para reducir incidencias."
        ahorro = merma * 0.20 + coste * 0.03
        condicion = "Cerrar controles APPCC y comparar incidencias durante una semana."
    else:
        recomendacion = "mantener_con_control"
        decision = "Mantener el plato con seguimiento de coste/racion y merma."
        ahorro = merma * 0.10
        condicion = "Revisar si entra en top de merma o coste dos semanas seguidas."

    if veces < 5:
        prioridad = "media" if prioridad in {"critica", "alta"} else prioridad
        condicion += " Muestra corta: validar con mas servicios."

    return prioridad, recomendacion, decision, round(ahorro, 2), condicion


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    ensure_schema(conn)
    if not table_exists(conn, "simulador_escandallos_diarios_plato"):
        return {"recomendaciones": 0, "omitido": "Faltan escandallos diarios."}

    conn.execute(
        "DELETE FROM simulador_recomendaciones_cocina_margen WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )

    rows = conn.execute(
        """
        SELECT receta_id, plato,
               COUNT(*) veces,
               GROUP_CONCAT(DISTINCT servicio) servicios,
               ROUND(SUM(coste_real), 2) coste_total,
               ROUND(AVG(coste_racion_real), 2) coste_racion,
               ROUND(SUM(coste_merma_estimado), 2) merma_total,
               ROUND(SUM(sobrante_raciones), 1) sobrante_raciones,
               ROUND(SUM(desviacion_coste), 2) desviacion_total,
               ROUND(AVG(desviacion_pct), 1) desviacion_pct_media,
               SUM(CASE WHEN riesgo='alto' THEN 1 ELSE 0 END) riesgo_alto
        FROM simulador_escandallos_diarios_plato
        WHERE corrida_id=? AND centro_id=?
        GROUP BY receta_id, plato
        HAVING merma_total > 1500 OR coste_total > 25000 OR riesgo_alto > 10
        ORDER BY merma_total DESC, coste_total DESC
        """,
        (corrida_id, centro_id),
    ).fetchall()

    inserted = 0
    ahorro_total = 0.0
    for row in rows:
        prioridad, recomendacion, decision, ahorro, condicion = classify(row)
        ahorro_total += ahorro
        datos = {
            "coste_total": row["coste_total"],
            "coste_racion": row["coste_racion"],
            "merma_total": row["merma_total"],
            "sobrante_raciones": row["sobrante_raciones"],
            "desviacion_total": row["desviacion_total"],
            "riesgo_alto": row["riesgo_alto"],
        }
        conn.execute(
            """
            INSERT INTO simulador_recomendaciones_cocina_margen
            (corrida_id, centro_id, receta_id, plato, veces, servicios,
             coste_total, coste_racion, merma_total, sobrante_raciones,
             desviacion_total, desviacion_pct_media, riesgo_alto, prioridad,
             recomendacion, decision_propuesta, ahorro_estimado,
             condicion_control, datos_json, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                centro_id,
                row["receta_id"],
                row["plato"],
                row["veces"],
                row["servicios"],
                row["coste_total"],
                row["coste_racion"],
                row["merma_total"],
                row["sobrante_raciones"],
                row["desviacion_total"],
                row["desviacion_pct_media"],
                row["riesgo_alto"],
                prioridad,
                recomendacion,
                decision,
                ahorro,
                condicion,
                json.dumps(datos, ensure_ascii=True),
                EMPRESA_ID,
            ),
        )
        inserted += 1

    top = conn.execute(
        """
        SELECT *
        FROM simulador_recomendaciones_cocina_margen
        WHERE corrida_id=? AND centro_id=?
        ORDER BY CASE prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END,
                 ahorro_estimado DESC
        LIMIT 1
        """,
        (corrida_id, centro_id),
    ).fetchone()
    if top and table_exists(conn, "simulador_vida_decisiones"):
        conn.execute(
            """
            INSERT INTO simulador_vida_decisiones
            (corrida_id, modulo, prioridad, titulo, diagnostico,
             decision_recomendada, impacto_estimado, datos_soporte_json,
             estado, empresa_id, centro_id)
            VALUES (?, 'cocina', ?, ?, ?, ?, ?, ?, 'pendiente', ?, ?)
            """,
            (
                corrida_id,
                top["prioridad"],
                f"Plato con impacto de margen: {top['plato']}",
                f"Coste acumulado {top['coste_total']:.2f} EUR, merma {top['merma_total']:.2f} EUR y coste/racion {top['coste_racion']:.2f} EUR.",
                top["decision_propuesta"],
                f"Ahorro estimado si se controla: {top['ahorro_estimado']:.2f} EUR.",
                top["datos_json"],
                EMPRESA_ID,
                centro_id,
            ),
        )

    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) recomendaciones,
               SUM(CASE WHEN prioridad='critica' THEN 1 ELSE 0 END) criticas,
               SUM(CASE WHEN prioridad='alta' THEN 1 ELSE 0 END) altas,
               SUM(CASE WHEN prioridad='media' THEN 1 ELSE 0 END) medias,
               ROUND(SUM(ahorro_estimado), 2) ahorro_estimado
        FROM simulador_recomendaciones_cocina_margen
        WHERE corrida_id=? AND centro_id=?
        """,
        (corrida_id, centro_id),
    ).fetchone()
    return dict(resumen) | {"insertados": inserted, "ahorro_total": round(ahorro_total, 2)}


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

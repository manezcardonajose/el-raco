# -*- coding: utf-8 -*-
"""Aplica una politica simulada para reducir merma recurrente.

No borra datos: deja una tabla de trazabilidad y ajusta lineas de produccion,
ingredientes reales y controles APPCC para probar si la ERP detecta mejoras.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3

ALTO_RIESGO = (
    "pescado",
    "gamb",
    "marisco",
    "langost",
    "mejillon",
    "pulpo",
    "huevo",
    "mayonesa",
    "crema",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reducir merma recurrente en simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--desde", default="")
    parser.add_argument("--merma-min", type=float, default=25.0)
    parser.add_argument("--limite", type=int, default=400)
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


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def latest_corrida(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "simulador_vida_corridas"):
        return 0
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_plan_reduccion_merma (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            fecha TEXT,
            servicio TEXT,
            linea_produccion_id INTEGER,
            receta_id INTEGER,
            plato TEXT,
            sobrante_original REAL DEFAULT 0,
            sobrante_nuevo REAL DEFAULT 0,
            raciones_originales REAL DEFAULT 0,
            raciones_nuevas REAL DEFAULT 0,
            coste_merma_original REAL DEFAULT 0,
            destino_sobrante TEXT,
            factor_ajuste REAL DEFAULT 1,
            decision TEXT,
            nota TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, linea_produccion_id)
        )
        """
    )


def infer_desde(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> str:
    row = conn.execute(
        """
        SELECT MAX(fecha) AS fecha
        FROM simulador_escandallos_diarios_plato
        WHERE corrida_id=? AND centro_id=?
        """,
        (corrida_id, centro_id),
    ).fetchone()
    if not row or not row["fecha"]:
        return "1900-01-01"
    fecha = datetime.strptime(row["fecha"], "%Y-%m-%d").date()
    return str(fecha - timedelta(days=45))


def is_high_risk_food(plato: str, categoria: str) -> bool:
    text = f"{plato or ''} {categoria or ''}".lower()
    return any(token in text for token in ALTO_RIESGO)


def aplicar(conn: sqlite3.Connection, corrida_id: int, centro_id: int, desde: str, merma_min: float, limite: int) -> dict:
    ensure_schema(conn)
    required = {
        "simulador_escandallos_diarios_plato",
        "lineas_produccion",
        "produccion_controles_appcc",
        "produccion_ingredientes_reales",
    }
    if not all(table_exists(conn, table) for table in required):
        return {"actualizados": 0, "omitido": "Faltan tablas de produccion o escandallos."}

    lp_cols = columns(conn, "lineas_produccion")
    pir_cols = columns(conn, "produccion_ingredientes_reales")
    pc_cols = columns(conn, "produccion_controles_appcc")
    desde = desde or infer_desde(conn, corrida_id, centro_id)

    rows = conn.execute(
        """
        WITH repeticion AS (
            SELECT receta_id, COUNT(*) veces, SUM(coste_merma_estimado) coste_merma
            FROM simulador_escandallos_diarios_plato
            WHERE corrida_id=? AND centro_id=? AND fecha>=? AND coste_merma_estimado>=?
            GROUP BY receta_id
        )
        SELECT e.*, COALESCE(r.veces, 1) veces_plato
        FROM simulador_escandallos_diarios_plato e
        JOIN repeticion r ON r.receta_id=e.receta_id
        WHERE e.corrida_id=? AND e.centro_id=? AND e.fecha>=? AND e.coste_merma_estimado>=?
        ORDER BY r.veces DESC, e.coste_merma_estimado DESC
        LIMIT ?
        """,
        (corrida_id, centro_id, desde, merma_min, corrida_id, centro_id, desde, merma_min, limite),
    ).fetchall()

    actualizados = 0
    reaprovechados = 0
    reduccion_raciones = 0.0
    merma_original = 0.0
    for row in rows:
        linea_id = int(row["linea_produccion_id"])
        if conn.execute(
            """
            SELECT 1
            FROM simulador_plan_reduccion_merma
            WHERE corrida_id=? AND centro_id=? AND linea_produccion_id=?
            """,
            (corrida_id, centro_id, linea_id),
        ).fetchone():
            continue

        sobrante_original = float(row["sobrante_raciones"] or 0)
        raciones_originales = float(row["raciones_reales"] or 0)
        if sobrante_original <= 0 or raciones_originales <= 0:
            continue

        alto_riesgo = is_high_risk_food(str(row["plato"] or ""), str(row["categoria"] or ""))
        repetido = int(row["veces_plato"] or 1)
        reduccion_pct = 0.45 if repetido >= 5 else 0.35
        if alto_riesgo:
            reduccion_pct = min(0.50, reduccion_pct + 0.05)

        raciones_reducidas = min(sobrante_original * reduccion_pct, raciones_originales * 0.18)
        raciones_nuevas = max(raciones_originales - raciones_reducidas, 1)
        factor = max(raciones_nuevas / raciones_originales, 0.55)
        sobrante_nuevo = max(sobrante_original - raciones_reducidas, 0)

        if not alto_riesgo and sobrante_nuevo <= 4:
            destino = "reaprovechamiento"
            decision = "Reducir produccion y guardar sobrante reaprovechable con trazabilidad APPCC."
            reaprovechados += 1
        else:
            destino = "merma_buffet"
            decision = "Reducir produccion futura; sobrante restante se marca como merma con motivo operativo."

        set_lp: list[str] = []
        params: list[float | int] = []
        for col in ("cantidad_real", "cantidad_producida", "raciones_planificadas", "cantidad_planificada"):
            if col in lp_cols:
                set_lp.append(f"{col}=ROUND(COALESCE({col},0) * ?, 3)")
                params.append(factor)
        if set_lp:
            params.append(linea_id)
            conn.execute(f"UPDATE lineas_produccion SET {', '.join(set_lp)} WHERE id=?", params)

        if "cantidad_real" in pir_cols:
            conn.execute(
                """
                UPDATE produccion_ingredientes_reales
                SET cantidad_real=ROUND(COALESCE(cantidad_real,0) * ?, 4)
                WHERE linea_produccion_id=?
                """,
                (factor, linea_id),
            )

        pc_updates = ["cantidad_sobrante=?"]
        pc_params: list[object] = [round(sobrante_nuevo, 3)]
        optional = {
            "destino_sobrante": destino,
            "motivo_merma": "Ajuste simulador: reduccion por merma recurrente",
            "metodo_conservacion": "abatido_refrigerado" if destino == "reaprovechamiento" else None,
            "requiere_regeneracion": 1 if destino == "reaprovechamiento" else 0,
            "dias_vida_util": 1 if destino == "reaprovechamiento" else None,
        }
        for col, value in optional.items():
            if col in pc_cols:
                pc_updates.append(f"{col}=?")
                pc_params.append(value)
        pc_params.append(linea_id)
        conn.execute(
            f"UPDATE produccion_controles_appcc SET {', '.join(pc_updates)} WHERE linea_produccion_id=?",
            pc_params,
        )

        conn.execute(
            """
            INSERT INTO simulador_plan_reduccion_merma
            (corrida_id, centro_id, fecha, servicio, linea_produccion_id, receta_id, plato,
             sobrante_original, sobrante_nuevo, raciones_originales, raciones_nuevas,
             coste_merma_original, destino_sobrante, factor_ajuste, decision, nota, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                centro_id,
                row["fecha"],
                row["servicio"],
                linea_id,
                row["receta_id"],
                row["plato"],
                round(sobrante_original, 3),
                round(sobrante_nuevo, 3),
                round(raciones_originales, 3),
                round(raciones_nuevas, 3),
                float(row["coste_merma_estimado"] or 0),
                destino,
                round(factor, 4),
                decision,
                f"Plato repetido {repetido} veces con merma >= {merma_min} EUR desde {desde}.",
                EMPRESA_ID,
            ),
        )
        actualizados += 1
        reduccion_raciones += raciones_reducidas
        merma_original += float(row["coste_merma_estimado"] or 0)

    conn.commit()
    return {
        "desde": desde,
        "candidatos": len(rows),
        "actualizados": actualizados,
        "reaprovechados": reaprovechados,
        "raciones_reducidas": round(reduccion_raciones, 2),
        "merma_original_afectada": round(merma_original, 2),
    }


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
        print(aplicar(conn, corrida_id, args.centro_id, args.desde, args.merma_min, args.limite))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

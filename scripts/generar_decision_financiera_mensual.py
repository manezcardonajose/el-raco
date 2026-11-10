# -*- coding: utf-8 -*-
"""Resume cada mes en una decision financiera operativa.

Une resultado mensual, mejor escenario MISE y rescates para que direccion vea
un solo semaforo: rentable, justo o perdida.
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
    parser = argparse.ArgumentParser(description="Generar decision financiera mensual.")
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
        CREATE TABLE IF NOT EXISTS simulador_decision_financiera_mensual (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            mes TEXT NOT NULL,
            ingresos_total REAL DEFAULT 0,
            coste_total_base REAL DEFAULT 0,
            margen_base REAL DEFAULT 0,
            margen_mise REAL DEFAULT 0,
            escenario_mise TEXT,
            margen_rescate REAL,
            escenario_rescate TEXT,
            margen_final_estimado REAL DEFAULT 0,
            estado TEXT,
            causa_principal TEXT,
            decision TEXT,
            indicador_clave TEXT,
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


def build_decision(row: sqlite3.Row, margen_final: float, estado: str, accion: str) -> tuple[str, str, str]:
    dias_negativos = int(row["dias_negativos"] or 0)
    ingresos = float(row["ingresos_total"] or 0)
    compras_pct = (float(row["coste_compras"] or 0) / ingresos * 100) if ingresos else 0.0
    laboral_pct = (float(row["coste_laboral"] or 0) / ingresos * 100) if ingresos else 0.0

    if estado == "verde":
        decision = "Mantener plan y vigilar desviaciones semanales."
    elif estado == "ambar":
        decision = "Aprobar solo con stop-loss semanal y cupos de compra."
    else:
        decision = "No aprobar asi: rehacer ventas, apertura, compras o estructura."

    if dias_negativos > 0:
        causa = f"{dias_negativos} dias negativos en el mes."
    elif compras_pct > 28:
        causa = f"Compras tensionadas: {compras_pct:.1f}% de ingresos."
    elif laboral_pct > 35:
        causa = f"Coste laboral alto: {laboral_pct:.1f}% de ingresos."
    elif margen_final < 0:
        causa = "El plan elegido no cubre estructura y operativa."
    else:
        causa = "Mes controlado, pero requiere seguimiento real."

    indicador = (
        f"Margen final {margen_final:.2f} EUR con accion {accion}. "
        f"Compras {compras_pct:.1f}% e laboral {laboral_pct:.1f}% sobre ingresos."
    )
    condicion = (
        "Revisar cada semana pickup, ADR neto, compras recibidas, mermas, "
        "fichajes, quejas y margen proyectado antes de abrir mas cupos."
    )
    return decision, causa, indicador if estado != "rojo" else indicador + " Requiere plan de rescate.", condicion


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    ensure_schema(conn)
    if not table_exists(conn, "simulador_resultado_mensual"):
        return {"meses": 0, "omitido": "Falta resultado mensual."}

    conn.execute(
        "DELETE FROM simulador_decision_financiera_mensual WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )

    rows = conn.execute(
        """
        WITH best_mise AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY mes
                ORDER BY CASE veredicto
                    WHEN 'recomendado' THEN 1
                    WHEN 'piloto_controlado' THEN 2
                    WHEN 'aplicar_con_cuidado' THEN 3
                    ELSE 4 END,
                    margen_escenario DESC
            ) rn
            FROM simulador_escenarios_mise_decision
            WHERE corrida_id=? AND centro_id=?
        ),
        best_rescue AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY mes
                ORDER BY CASE veredicto
                    WHEN 'recomendado' THEN 1
                    WHEN 'piloto_controlado' THEN 2
                    ELSE 3 END,
                    margen_estimado DESC
            ) rn
            FROM simulador_rescate_meses_criticos
            WHERE corrida_id=? AND centro_id=?
        )
        SELECT rm.*, bm.nombre AS escenario_mise,
               bm.margen_escenario AS margen_mise,
               br.accion AS escenario_rescate,
               br.margen_estimado AS margen_rescate
        FROM simulador_resultado_mensual rm
        LEFT JOIN best_mise bm ON bm.mes=rm.mes AND bm.rn=1
        LEFT JOIN best_rescue br ON br.mes=rm.mes AND br.rn=1
        WHERE rm.corrida_id=? AND rm.centro_id=?
        ORDER BY rm.mes
        """,
        (corrida_id, centro_id, corrida_id, centro_id, corrida_id, centro_id),
    ).fetchall()

    inserted = 0
    for row in rows:
        margen_mise = float(row["margen_mise"] if row["margen_mise"] is not None else row["margen"])
        margen_rescate = row["margen_rescate"]
        if margen_rescate is not None and float(margen_rescate) > margen_mise:
            margen_final = float(margen_rescate)
            accion = row["escenario_rescate"] or "rescate"
        else:
            margen_final = margen_mise
            accion = row["escenario_mise"] or "resultado base"
        estado = semaforo(margen_final, float(row["ingresos_total"] or 0))
        decision, causa, indicador, condicion = build_decision(row, margen_final, estado, accion)
        conn.execute(
            """
            INSERT INTO simulador_decision_financiera_mensual
            (corrida_id, centro_id, mes, ingresos_total, coste_total_base,
             margen_base, margen_mise, escenario_mise, margen_rescate,
             escenario_rescate, margen_final_estimado, estado, causa_principal,
             decision, indicador_clave, condicion_control, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                centro_id,
                row["mes"],
                row["ingresos_total"],
                row["coste_total"],
                row["margen"],
                round(margen_mise, 2),
                row["escenario_mise"],
                None if margen_rescate is None else round(float(margen_rescate), 2),
                row["escenario_rescate"],
                round(margen_final, 2),
                estado,
                causa,
                decision,
                indicador,
                condicion,
                EMPRESA_ID,
            ),
        )
        inserted += 1

    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) meses,
               SUM(CASE WHEN estado='verde' THEN 1 ELSE 0 END) verdes,
               SUM(CASE WHEN estado='ambar' THEN 1 ELSE 0 END) ambar,
               SUM(CASE WHEN estado='rojo' THEN 1 ELSE 0 END) rojos,
               ROUND(SUM(margen_final_estimado), 2) margen_final
        FROM simulador_decision_financiera_mensual
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

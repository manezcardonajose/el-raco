# -*- coding: utf-8 -*-
"""Genera limites de decision para no aprobar escenarios que rompen caja.

Convierte los rescates simulados en condiciones minimas de control: margen
de seguridad, ocupacion, ADR, TPV por pax, ahorro y parada operativa.
No modifica reservas, pedidos ni costes reales.
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
    parser = argparse.ArgumentParser(description="Generar guardarrailes de no arruinarse.")
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
        CREATE TABLE IF NOT EXISTS simulador_guardarrailes_no_arruinarse (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            mes TEXT NOT NULL,
            escenario TEXT NOT NULL,
            margen_base REAL DEFAULT 0,
            margen_estimado REAL DEFAULT 0,
            margen_seguridad_objetivo REAL DEFAULT 0,
            brecha_seguridad REAL DEFAULT 0,
            estado_margen TEXT,
            ocupacion_minima_pct REAL DEFAULT 0,
            adr_minimo REAL DEFAULT 0,
            tpv_pax_minimo REAL DEFAULT 0,
            ahorro_minimo REAL DEFAULT 0,
            compra_maxima REAL DEFAULT 0,
            ingreso_extra_minimo REAL DEFAULT 0,
            decision TEXT,
            condiciones_minimas TEXT,
            parada_si TEXT,
            lectura TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes, escenario)
        )
        """
    )


def estado_margen(margen: float, objetivo: float) -> tuple[str, str]:
    if margen >= objetivo:
        return "verde", "aplicar_controlado"
    if margen > 0:
        return "ambar", "piloto_con_stop_loss"
    return "rojo", "no_aplicar"


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    ensure_schema(conn)
    if not table_exists(conn, "simulador_rescate_meses_criticos"):
        return {"guardarrailes": 0, "omitido": "Faltan rescates de meses criticos."}

    conn.execute(
        "DELETE FROM simulador_guardarrailes_no_arruinarse WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )

    rows = conn.execute(
        """
        WITH best_rescue AS (
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
        ),
        best_mise AS (
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
        )
        SELECT br.mes, br.accion, br.descripcion, br.margen_base_mise,
               br.ingreso_delta, br.coste_delta, br.ahorro_delta,
               br.margen_estimado, br.ocupacion_objetivo_pct,
               br.adr_objetivo, br.tpv_pax_objetivo, br.riesgo,
               bm.coste_escenario, bm.nombre AS escenario_mise
        FROM best_rescue br
        LEFT JOIN best_mise bm ON bm.mes=br.mes AND bm.rn=1
        WHERE br.rn=1
        ORDER BY br.mes
        """,
        (corrida_id, centro_id, corrida_id, centro_id),
    ).fetchall()

    inserted = 0
    for row in rows:
        margen = float(row["margen_estimado"] or 0)
        ingresos_extra = float(row["ingreso_delta"] or 0)
        objetivo = max(30000.0, abs(float(row["margen_base_mise"] or 0)) * 0.12)
        estado, decision = estado_margen(margen, objetivo)
        brecha = max(0.0, objetivo - margen)
        compra_max = max(0.0, float(row["coste_escenario"] or 0) * (0.98 if estado == "ambar" else 1.0))
        occ = float(row["ocupacion_objetivo_pct"] or 0)
        adr = float(row["adr_objetivo"] or 0)
        tpv = float(row["tpv_pax_objetivo"] or 0)
        ahorro = float(row["ahorro_delta"] or 0)
        condiciones = (
            f"Ocupacion minima {occ:.1f}%, ADR neto minimo {adr:.2f} EUR, "
            f"TPV/pax minimo {tpv:.2f} EUR, ahorro minimo {ahorro:.2f} EUR, "
            f"compra maxima {compra_max:.2f} EUR."
        )
        parada = (
            "Parar o rehacer el plan si el pickup semanal no llega al objetivo, "
            "si el ADR neto cae por debajo del minimo, si compras supera el maximo, "
            "si suben quejas o si el margen proyectado queda por debajo del colchon."
        )
        if estado == "verde":
            lectura = "El escenario salva el mes con colchon. Aun asi se ejecuta por semanas y con compra controlada."
        elif estado == "ambar":
            lectura = "El escenario puede salvar el mes, pero va justo. Solo piloto con stop-loss y revision semanal."
        else:
            lectura = "El escenario no se debe aprobar como plan principal porque no protege caja."

        conn.execute(
            """
            INSERT INTO simulador_guardarrailes_no_arruinarse
            (corrida_id, centro_id, mes, escenario, margen_base,
             margen_estimado, margen_seguridad_objetivo, brecha_seguridad,
             estado_margen, ocupacion_minima_pct, adr_minimo, tpv_pax_minimo,
             ahorro_minimo, compra_maxima, ingreso_extra_minimo, decision,
             condiciones_minimas, parada_si, lectura, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                centro_id,
                row["mes"],
                row["accion"],
                row["margen_base_mise"],
                margen,
                round(objetivo, 2),
                round(brecha, 2),
                estado,
                round(occ, 1),
                round(adr, 2),
                round(tpv, 2),
                round(ahorro, 2),
                round(compra_max, 2),
                round(ingresos_extra, 2),
                decision,
                condiciones,
                parada,
                lectura,
                EMPRESA_ID,
            ),
        )
        inserted += 1

    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) guardarrailes,
               SUM(CASE WHEN estado_margen='verde' THEN 1 ELSE 0 END) verdes,
               SUM(CASE WHEN estado_margen='ambar' THEN 1 ELSE 0 END) ambar,
               SUM(CASE WHEN estado_margen='rojo' THEN 1 ELSE 0 END) rojos,
               ROUND(SUM(brecha_seguridad), 2) brecha_seguridad
        FROM simulador_guardarrailes_no_arruinarse
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

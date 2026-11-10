# -*- coding: utf-8 -*-
"""Audita la cadena de decisiones de margen del simulador.

Comprueba coherencia entre escandallos, recomendaciones, impacto mensual,
plan integral, guardarrailes y stop-loss.
"""

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
    parser = argparse.ArgumentParser(description="Auditar cerebro de margen de la simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def latest_corrida(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "simulador_vida_corridas"):
        return 0
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def scalar(conn: sqlite3.Connection, sql: str, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        return row[0] if row[0] is not None else default
    except Exception:
        return default


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_auditoria_cerebro_margen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            area TEXT,
            severidad TEXT,
            codigo TEXT,
            titulo TEXT,
            detalle TEXT,
            evidencia_json TEXT,
            estado TEXT DEFAULT 'abierta',
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_auditoria_cerebro_margen_resumen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            total INTEGER DEFAULT 0,
            criticas INTEGER DEFAULT 0,
            altas INTEGER DEFAULT 0,
            medias INTEGER DEFAULT 0,
            bajas INTEGER DEFAULT 0,
            score REAL DEFAULT 0,
            lectura TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def add(issues: list[dict], area: str, severity: str, code: str, title: str, detail: str, evidence=None) -> None:
    issues.append({
        "area": area,
        "severidad": severity,
        "codigo": code,
        "titulo": title,
        "detalle": detail,
        "evidencia": evidence or {},
    })


def audit_required_tables(conn: sqlite3.Connection, issues: list[dict]) -> None:
    required = [
        "simulador_decision_financiera_mensual",
        "simulador_escandallos_diarios_plato",
        "simulador_recomendaciones_cocina_margen",
        "simulador_impacto_cocina_mensual",
        "simulador_plan_integral_margen",
        "simulador_stoploss_semanal",
    ]
    for table in required:
        if not table_exists(conn, table):
            add(issues, "estructura", "critica", f"sin_{table}", f"Falta {table}", "La cadena de decisiones no esta completa.")


def audit_cocina_chain(conn: sqlite3.Connection, corrida_id: int, centro_id: int, issues: list[dict]) -> None:
    if not table_exists(conn, "simulador_escandallos_diarios_plato"):
        return
    esc = int(scalar(conn, "SELECT COUNT(*) FROM simulador_escandallos_diarios_plato WHERE corrida_id=? AND centro_id=?", (corrida_id, centro_id)))
    if esc <= 0:
        add(issues, "cocina", "critica", "sin_escandallos", "No hay escandallos diarios", "Sin escandallos no se puede explicar coste por plato.")
    sin_coste = int(scalar(conn, """
        SELECT COUNT(*) FROM simulador_escandallos_diarios_plato
        WHERE corrida_id=? AND centro_id=? AND raciones_reales>0 AND coste_real<=0
    """, (corrida_id, centro_id)))
    if sin_coste:
        add(issues, "cocina", "alta", "platos_sin_coste", "Platos producidos sin coste real", f"Hay {sin_coste} lineas con raciones pero coste real 0.")
    coste_racion_extremo = int(scalar(conn, """
        SELECT COUNT(*) FROM simulador_escandallos_diarios_plato
        WHERE corrida_id=? AND centro_id=? AND coste_racion_real>30
    """, (corrida_id, centro_id)))
    if coste_racion_extremo:
        add(issues, "cocina", "media", "coste_racion_extremo", "Coste por racion muy alto", f"Hay {coste_racion_extremo} lineas con coste/racion > 30 EUR.")

    if table_exists(conn, "simulador_recomendaciones_cocina_margen"):
        recs = int(scalar(conn, "SELECT COUNT(*) FROM simulador_recomendaciones_cocina_margen WHERE corrida_id=? AND centro_id=?", (corrida_id, centro_id)))
        if esc > 0 and recs <= 0:
            add(issues, "cocina", "alta", "sin_recomendaciones", "Escandallos sin recomendaciones", "Hay costes por plato pero no propuestas de cocina.")
        orphan = int(scalar(conn, """
            SELECT COUNT(*)
            FROM simulador_recomendaciones_cocina_margen r
            WHERE r.corrida_id=? AND r.centro_id=?
              AND NOT EXISTS (
                SELECT 1 FROM simulador_escandallos_diarios_plato e
                WHERE e.corrida_id=r.corrida_id AND e.centro_id=r.centro_id AND e.receta_id=r.receta_id
              )
        """, (corrida_id, centro_id)))
        if orphan:
            add(issues, "cocina", "alta", "recomendacion_sin_escandallo", "Recomendaciones sin escandallo origen", f"Hay {orphan} recomendaciones sin lineas de escandallo.")


def audit_finance_chain(conn: sqlite3.Connection, corrida_id: int, centro_id: int, issues: list[dict]) -> None:
    if not table_exists(conn, "simulador_decision_financiera_mensual"):
        return
    ambar = int(scalar(conn, """
        SELECT COUNT(*) FROM simulador_decision_financiera_mensual
        WHERE corrida_id=? AND centro_id=? AND estado IN ('ambar','rojo')
    """, (corrida_id, centro_id)))
    impacto = int(scalar(conn, "SELECT COUNT(*) FROM simulador_impacto_cocina_mensual WHERE corrida_id=? AND centro_id=?", (corrida_id, centro_id))) if table_exists(conn, "simulador_impacto_cocina_mensual") else 0
    if impacto <= 0:
        add(issues, "finanzas", "alta", "sin_impacto_cocina", "Falta impacto mensual de cocina", "No se sabe si las propuestas de cocina cambian margen mensual.")
    if table_exists(conn, "simulador_impacto_cocina_mensual"):
        imposibles = int(scalar(conn, """
            SELECT COUNT(*) FROM simulador_impacto_cocina_mensual
            WHERE corrida_id=? AND centro_id=? AND ABS((margen_antes+ahorro_estimado)-margen_despues)>1
        """, (corrida_id, centro_id)))
        if imposibles:
            add(issues, "finanzas", "critica", "impacto_no_cuadra", "Impacto cocina no cuadra", f"Hay {imposibles} meses donde margen antes + ahorro no coincide con despues.")

    if table_exists(conn, "simulador_plan_integral_margen"):
        sin_plan = int(scalar(conn, """
            SELECT COUNT(*)
            FROM simulador_impacto_cocina_mensual ic
            WHERE ic.corrida_id=? AND ic.centro_id=? AND ic.estado_despues!='verde'
              AND NOT EXISTS (
                SELECT 1 FROM simulador_plan_integral_margen p
                WHERE p.corrida_id=ic.corrida_id AND p.centro_id=ic.centro_id AND p.mes=ic.mes
              )
        """, (corrida_id, centro_id)))
        if sin_plan:
            add(issues, "finanzas", "critica", "mes_riesgo_sin_plan", "Mes en riesgo sin plan integral", f"Hay {sin_plan} meses que siguen en riesgo tras cocina y no tienen plan integral.")
        planes_sin_brecha = int(scalar(conn, """
            SELECT COUNT(*) FROM simulador_plan_integral_margen
            WHERE corrida_id=? AND centro_id=? AND brecha_margen<=0
        """, (corrida_id, centro_id)))
        if planes_sin_brecha:
            add(issues, "finanzas", "media", "plan_sin_brecha", "Plan integral sin brecha", f"Hay {planes_sin_brecha} planes que podrian sobrar porque no tienen brecha.")
    elif ambar:
        add(issues, "finanzas", "critica", "sin_plan_integral", "Meses en riesgo sin plan integral", f"Hay {ambar} meses ambar/rojo y no existe tabla de plan integral.")


def audit_stoploss(conn: sqlite3.Connection, corrida_id: int, centro_id: int, issues: list[dict]) -> None:
    if not table_exists(conn, "simulador_stoploss_semanal"):
        return
    rows = int(scalar(conn, "SELECT COUNT(*) FROM simulador_stoploss_semanal WHERE corrida_id=? AND centro_id=?", (corrida_id, centro_id)))
    if rows <= 0:
        add(issues, "control", "alta", "sin_stoploss", "No hay stop-loss semanal", "No se puede detectar si el mes se rompe antes del cierre.")
    incoherentes = int(scalar(conn, """
        SELECT COUNT(*) FROM simulador_stoploss_semanal
        WHERE corrida_id=? AND centro_id=?
          AND estado='verde' AND incumplimientos>0
    """, (corrida_id, centro_id)))
    if incoherentes:
        add(issues, "control", "alta", "stoploss_verde_con_fallos", "Stop-loss verde con incumplimientos", f"Hay {incoherentes} semanas verdes con fallos.")
    sin_mes = int(scalar(conn, """
        SELECT COUNT(*)
        FROM simulador_stoploss_semanal s
        WHERE s.corrida_id=? AND s.centro_id=?
          AND NOT EXISTS (
            SELECT 1 FROM simulador_decision_financiera_mensual d
            WHERE d.corrida_id=s.corrida_id AND d.centro_id=s.centro_id AND d.mes=s.mes
          )
    """, (corrida_id, centro_id)))
    if sin_mes:
        add(issues, "control", "media", "stoploss_mes_fuera_campana", "Stop-loss de mes fuera de decision mensual", f"Hay {sin_mes} semanas sin mes financiero asociado.")


def score_from(issues: list[dict]) -> float:
    penalty = 0
    for issue in issues:
        penalty += {"critica": 25, "alta": 12, "media": 6, "baja": 2}.get(issue["severidad"], 3)
    return max(0.0, 100.0 - penalty)


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    ensure_schema(conn)
    issues: list[dict] = []
    audit_required_tables(conn, issues)
    audit_cocina_chain(conn, corrida_id, centro_id, issues)
    audit_finance_chain(conn, corrida_id, centro_id, issues)
    audit_stoploss(conn, corrida_id, centro_id, issues)

    conn.execute("DELETE FROM simulador_auditoria_cerebro_margen WHERE corrida_id=? AND centro_id=?", (corrida_id, centro_id))
    conn.execute("DELETE FROM simulador_auditoria_cerebro_margen_resumen WHERE corrida_id=? AND centro_id=?", (corrida_id, centro_id))
    for issue in issues:
        conn.execute(
            """
            INSERT INTO simulador_auditoria_cerebro_margen
            (corrida_id, centro_id, area, severidad, codigo, titulo, detalle,
             evidencia_json, estado, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'abierta', ?)
            """,
            (
                corrida_id,
                centro_id,
                issue["area"],
                issue["severidad"],
                issue["codigo"],
                issue["titulo"],
                issue["detalle"],
                json.dumps(issue["evidencia"], ensure_ascii=True),
                EMPRESA_ID,
            ),
        )

    total = len(issues)
    criticas = sum(1 for i in issues if i["severidad"] == "critica")
    altas = sum(1 for i in issues if i["severidad"] == "alta")
    medias = sum(1 for i in issues if i["severidad"] == "media")
    bajas = sum(1 for i in issues if i["severidad"] == "baja")
    score = score_from(issues)
    if criticas:
        lectura = "El cerebro de margen tiene roturas criticas: no usar decisiones sin revisar."
    elif altas:
        lectura = "El cerebro de margen funciona, pero hay datos altos que revisar."
    elif medias:
        lectura = "El cerebro de margen es util con pequeñas cautelas."
    else:
        lectura = "Cadena de margen coherente: cocina, impacto, plan integral y stop-loss conectan."
    conn.execute(
        """
        INSERT INTO simulador_auditoria_cerebro_margen_resumen
        (corrida_id, centro_id, total, criticas, altas, medias, bajas, score,
         lectura, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (corrida_id, centro_id, total, criticas, altas, medias, bajas, score, lectura, EMPRESA_ID),
    )
    conn.commit()
    return {
        "corrida_id": corrida_id,
        "centro_id": centro_id,
        "total": total,
        "criticas": criticas,
        "altas": altas,
        "medias": medias,
        "bajas": bajas,
        "score": score,
        "lectura": lectura,
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
        print(json.dumps(generar(conn, corrida_id, args.centro_id), ensure_ascii=True, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

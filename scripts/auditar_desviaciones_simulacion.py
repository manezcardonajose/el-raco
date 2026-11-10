"""Audita desviaciones entre vida simulada, produccion, APPCC y compras.

La idea es que el simulador no solo genere datos: tambien debe decirnos si esos
datos tienen sentido operativo para un hotel real.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DEFAULT = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = (), default=0):
    row = conn.execute(sql, params).fetchone()
    if not row:
        return default
    value = row[0]
    return default if value is None else value


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_desviaciones_operativas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            fecha TEXT,
            area TEXT NOT NULL,
            nivel TEXT NOT NULL,
            codigo TEXT NOT NULL,
            titulo TEXT NOT NULL,
            detalle TEXT,
            valor REAL DEFAULT 0,
            referencia REAL DEFAULT 0,
            accion_recomendada TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_desviaciones_resumen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            fecha_desde TEXT,
            fecha_hasta TEXT,
            total INTEGER DEFAULT 0,
            criticas INTEGER DEFAULT 0,
            altas INTEGER DEFAULT 0,
            medias INTEGER DEFAULT 0,
            score REAL DEFAULT 0,
            lectura TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def add(issues: list[dict], fecha: str | None, area: str, nivel: str, codigo: str, titulo: str, detalle: str, valor: float = 0.0, referencia: float = 0.0, accion: str = "") -> None:
    issues.append(
        {
            "fecha": fecha,
            "area": area,
            "nivel": nivel,
            "codigo": codigo,
            "titulo": titulo,
            "detalle": detalle,
            "valor": round(float(valor or 0), 3),
            "referencia": round(float(referencia or 0), 3),
            "accion": accion,
        }
    )


def audit_calendar(conn: sqlite3.Connection, corrida_id: int, centro_id: int, issues: list[dict]) -> None:
    if not table_exists(conn, "simulador_vida_kpis_diarios") or not table_exists(conn, "simulador_produccion_resumen_diario"):
        add(issues, None, "calendario", "critica", "tablas_base_faltan", "Faltan tablas base", "No se puede cruzar vida simulada con produccion.")
        return
    prod_sin_kpi = conn.execute(
        """
        SELECT p.fecha
        FROM simulador_produccion_resumen_diario p
        LEFT JOIN simulador_vida_kpis_diarios k
          ON k.fecha=p.fecha AND k.corrida_id=p.corrida_id AND k.centro_id=p.centro_id
        WHERE p.corrida_id=? AND p.centro_id=? AND k.id IS NULL
        ORDER BY p.fecha
        """,
        (corrida_id, centro_id),
    ).fetchall()
    for row in prod_sin_kpi:
        add(
            issues,
            row["fecha"],
            "calendario",
            "alta",
            "produccion_sin_kpi",
            "Produccion sin ocupacion/KPI",
            "Hay produccion generada pero el dia no tiene KPI de ocupacion, ingresos ni margen.",
            accion="Crear KPI del dia o no generar produccion fuera del calendario vivo.",
        )

    kpi_sin_prod = conn.execute(
        """
        SELECT k.fecha, k.pax
        FROM simulador_vida_kpis_diarios k
        LEFT JOIN simulador_produccion_resumen_diario p
          ON p.fecha=k.fecha AND p.corrida_id=k.corrida_id AND p.centro_id=k.centro_id
        WHERE k.corrida_id=? AND k.centro_id=? AND COALESCE(k.pax,0)>0 AND p.id IS NULL
        ORDER BY k.fecha
        """,
        (corrida_id, centro_id),
    ).fetchall()
    for row in kpi_sin_prod:
        add(
            issues,
            row["fecha"],
            "calendario",
            "critica",
            "pax_sin_produccion",
            "Clientes sin produccion",
            f"Hay {int(row['pax'] or 0)} pax previstos pero no existe produccion para el dia.",
            valor=row["pax"],
            accion="Generar partes de produccion y salida de camara para ese dia.",
        )


def audit_production_vs_pax(conn: sqlite3.Connection, corrida_id: int, centro_id: int, issues: list[dict]) -> None:
    rows = conn.execute(
        """
        SELECT
          k.fecha,
          COALESCE(k.pax,0) pax,
          COALESCE(k.coste_compras_estimado,0) coste_compras,
          COALESCE(p.lineas,0) lineas,
          COALESCE(p.ingredientes_reales,0) ingredientes,
          COALESCE(p.controles_appcc,0) controles,
          COALESCE(p.incidencias,0) incidencias,
          COALESCE(p.kg_carnes,0)+COALESCE(p.kg_pescados,0)+COALESCE(p.kg_otros,0) kg_total
        FROM simulador_vida_kpis_diarios k
        JOIN simulador_produccion_resumen_diario p
          ON p.fecha=k.fecha AND p.corrida_id=k.corrida_id AND p.centro_id=k.centro_id
        WHERE k.corrida_id=? AND k.centro_id=?
        ORDER BY k.fecha
        """,
        (corrida_id, centro_id),
    ).fetchall()
    for row in rows:
        pax = float(row["pax"] or 0)
        lineas = float(row["lineas"] or 0)
        kg_total = float(row["kg_total"] or 0)
        kg_pax = kg_total / pax if pax else 0
        incid_ratio = float(row["incidencias"] or 0) / lineas if lineas else 0
        appcc_ratio = float(row["controles"] or 0) / lineas if lineas else 0
        ingredientes_linea = float(row["ingredientes"] or 0) / lineas if lineas else 0

        if kg_pax > 2.2:
            nivel = "critica" if kg_pax > 3.2 else "alta"
            add(
                issues,
                row["fecha"],
                "produccion",
                nivel,
                "kg_por_pax_alto",
                "Produccion excesiva por pax",
                f"Produccion: {kg_total:.2f} kg para {int(pax)} pax ({kg_pax:.2f} kg/pax).",
                kg_pax,
                1.6,
                "Revisar escalado de recetas, raciones por servicio y mermas previstas.",
            )
        if kg_pax < 0.65 and pax > 20:
            add(
                issues,
                row["fecha"],
                "produccion",
                "media",
                "kg_por_pax_bajo",
                "Produccion corta para el volumen de clientes",
                f"Produccion: {kg_total:.2f} kg para {int(pax)} pax ({kg_pax:.2f} kg/pax).",
                kg_pax,
                0.9,
                "Revisar si faltan partes, servicios o recetas asignadas al menu.",
            )
        if appcc_ratio < 0.95 and lineas > 0:
            add(
                issues,
                row["fecha"],
                "appcc",
                "alta",
                "appcc_incompleto",
                "Controles APPCC incompletos",
                f"{int(row['controles'] or 0)} controles para {int(lineas)} lineas.",
                appcc_ratio,
                1.0,
                "Toda linea producida debe tener control APPCC asociado.",
            )
        if ingredientes_linea < 2.0 and lineas > 0:
            add(
                issues,
                row["fecha"],
                "produccion",
                "media",
                "ingredientes_linea_bajos",
                "Lineas con pocos ingredientes reales",
                f"Media de {ingredientes_linea:.2f} ingredientes reales por linea.",
                ingredientes_linea,
                3.0,
                "Revisar recetas vacias o productos no enlazados a ingredientes.",
            )
        if incid_ratio > 0.24 and lineas > 0:
            add(
                issues,
                row["fecha"],
                "operativa",
                "alta",
                "incidencias_produccion_altas",
                "Demasiadas incidencias de produccion",
                f"{int(row['incidencias'] or 0)} incidencias sobre {int(lineas)} lineas.",
                incid_ratio,
                0.15,
                "Separar incidencia real de ruido simulado y revisar temperaturas/mermas.",
            )
        if kg_total > 0 and float(row["coste_compras"] or 0) <= 0:
            add(
                issues,
                row["fecha"],
                "compras",
                "media",
                "produccion_sin_coste_compras",
                "Produccion sin coste de compras",
                "Hay consumo de producto, pero el KPI diario no refleja coste de compras.",
                0,
                1,
                "Arrastrar coste desde movimientos de stock, recepciones o escandallos.",
            )


def save_report(conn: sqlite3.Connection, corrida_id: int, centro_id: int, issues: list[dict]) -> dict:
    conn.execute(
        "DELETE FROM simulador_desviaciones_operativas WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )
    conn.execute(
        "DELETE FROM simulador_desviaciones_resumen WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )
    for item in issues:
        conn.execute(
            """
            INSERT INTO simulador_desviaciones_operativas
            (corrida_id, fecha, area, nivel, codigo, titulo, detalle, valor, referencia,
             accion_recomendada, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                item["fecha"],
                item["area"],
                item["nivel"],
                item["codigo"],
                item["titulo"],
                item["detalle"],
                item["valor"],
                item["referencia"],
                item["accion"],
                EMPRESA_ID,
                centro_id,
            ),
        )
    criticas = sum(1 for i in issues if i["nivel"] == "critica")
    altas = sum(1 for i in issues if i["nivel"] == "alta")
    medias = sum(1 for i in issues if i["nivel"] == "media")
    score = max(0, 100 - criticas * 12 - altas * 5 - medias * 2)
    fecha_desde = scalar(conn, "SELECT MIN(fecha) FROM simulador_vida_kpis_diarios WHERE corrida_id=? AND centro_id=?", (corrida_id, centro_id), None)
    fecha_hasta = scalar(conn, "SELECT MAX(fecha) FROM simulador_vida_kpis_diarios WHERE corrida_id=? AND centro_id=?", (corrida_id, centro_id), None)
    if criticas:
        lectura = "La simulacion se desvia: hay errores criticos que invalidan decisiones."
    elif altas:
        lectura = "La simulacion sirve para probar, pero hay desviaciones altas que ajustar."
    elif medias:
        lectura = "La simulacion esta estable, con detalles medios pendientes."
    else:
        lectura = "La simulacion esta alineada: vida, produccion y KPIs cuadran razonablemente."
    conn.execute(
        """
        INSERT INTO simulador_desviaciones_resumen
        (corrida_id, fecha_desde, fecha_hasta, total, criticas, altas, medias, score,
         lectura, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (corrida_id, fecha_desde, fecha_hasta, len(issues), criticas, altas, medias, score, lectura, EMPRESA_ID, centro_id),
    )
    conn.commit()
    return {
        "corrida_id": corrida_id,
        "centro_id": centro_id,
        "fecha_desde": fecha_desde,
        "fecha_hasta": fecha_hasta,
        "total": len(issues),
        "criticas": criticas,
        "altas": altas,
        "medias": medias,
        "score": score,
        "lectura": lectura,
        "top": issues[:12],
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--corrida-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        issues: list[dict] = []
        audit_calendar(conn, args.corrida_id, args.centro_id, issues)
        audit_production_vs_pax(conn, args.corrida_id, args.centro_id, issues)
        report = save_report(conn, args.corrida_id, args.centro_id, issues)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

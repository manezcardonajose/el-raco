# -*- coding: utf-8 -*-
"""Genera propuestas IA mensuales desde la simulacion.

Convierte datos economicos y operativos en decisiones: campanas, cupos,
precio minimo, upselling y ajustes de estructura. No borra datos operativos.
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
    parser = argparse.ArgumentParser(description="Generar propuestas IA mensuales para la simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def latest_corrida(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def dinero(value: float) -> float:
    return round(float(value or 0), 2)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_ia_propuestas_mensuales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            mes TEXT,
            area TEXT,
            prioridad TEXT,
            titulo TEXT,
            diagnostico TEXT,
            propuesta TEXT,
            impacto_estimado TEXT,
            condicion_control TEXT,
            soporte_json TEXT,
            estado TEXT DEFAULT 'propuesta',
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes, area, titulo)
        )
        """
    )


def habitaciones_capacidad(conn: sqlite3.Connection, centro_id: int) -> int:
    for table in ("habitaciones_recepcion", "habitaciones"):
        if not table_exists(conn, table):
            continue
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        where = "1=1"
        params: list[object] = []
        if "centro_id" in cols:
            where += " AND centro_id=?"
            params.append(centro_id)
        if "empresa_id" in cols:
            where += " AND empresa_id=?"
            params.append(EMPRESA_ID)
        row = conn.execute(f"SELECT COUNT(*) total FROM {table} WHERE {where}", params).fetchone()
        if row and int(row["total"] or 0):
            return int(row["total"])
    return 220


def contextos_mes(conn: sqlite3.Connection, corrida_id: int, mes: str) -> list[dict]:
    if not table_exists(conn, "simulador_contexto_externo"):
        return []
    rows = conn.execute(
        """
        SELECT tipo, titulo, intensidad, descripcion
        FROM simulador_contexto_externo
        WHERE corrida_id=?
          AND substr(fecha_inicio,1,7) <= ?
          AND substr(fecha_fin,1,7) >= ?
        ORDER BY intensidad DESC
        LIMIT 6
        """,
        (corrida_id, mes, mes),
    ).fetchall()
    return [dict(r) for r in rows]


def insert_propuesta(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        """
        INSERT INTO simulador_ia_propuestas_mensuales
        (corrida_id, centro_id, mes, area, prioridad, titulo, diagnostico, propuesta,
         impacto_estimado, condicion_control, soporte_json, estado, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'propuesta', ?)
        ON CONFLICT(corrida_id, centro_id, mes, area, titulo) DO UPDATE SET
            prioridad=excluded.prioridad,
            diagnostico=excluded.diagnostico,
            propuesta=excluded.propuesta,
            impacto_estimado=excluded.impacto_estimado,
            condicion_control=excluded.condicion_control,
            soporte_json=excluded.soporte_json,
            estado='propuesta'
        """,
        (
            data["corrida_id"],
            data["centro_id"],
            data["mes"],
            data["area"],
            data["prioridad"],
            data["titulo"],
            data["diagnostico"],
            data["propuesta"],
            data["impacto_estimado"],
            data["condicion_control"],
            json.dumps(data["soporte"], ensure_ascii=False),
            EMPRESA_ID,
        ),
    )


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
        capacidad = habitaciones_capacidad(conn, CENTRO_ID)
        meses = conn.execute(
            """
            SELECT rm.*,
                   COALESCE(SUM(k.habitaciones_ocupadas), 0) habitaciones_vendidas,
                   COALESCE(SUM(k.pax), 0) pax,
                   COALESCE(SUM(k.ingresos_habitaciones), 0) ingresos_hab,
                   COALESCE(SUM(k.ingresos_tpv), 0) ingresos_tpv
            FROM simulador_resultado_mensual rm
            LEFT JOIN simulador_vida_kpis_diarios k
              ON k.corrida_id=rm.corrida_id AND k.centro_id=rm.centro_id AND substr(k.fecha,1,7)=rm.mes
            WHERE rm.corrida_id=? AND rm.centro_id=?
            GROUP BY rm.id
            ORDER BY rm.mes
            """,
            (corrida_id, CENTRO_ID),
        ).fetchall()
        creadas = 0
        for mes in meses:
            dias = max(int(mes["dias_simulados"] or 1), 1)
            hab_vendidas = float(mes["habitaciones_vendidas"] or 0)
            pax = float(mes["pax"] or 0)
            ocupacion = hab_vendidas / max(capacidad * dias, 1)
            adr = float(mes["ingresos_hab"] or 0) / hab_vendidas if hab_vendidas else 0
            tpv_pax = float(mes["ingresos_tpv"] or 0) / pax if pax else 0
            margen = float(mes["margen"] or 0)
            punto_muerto = float(mes["punto_muerto_diario"] or 0)
            ingreso_diario = float(mes["ingresos_total"] or 0) / dias
            brecha_diaria = max(punto_muerto - ingreso_diario, 0)
            soporte = {
                "capacidad_habitaciones": capacidad,
                "ocupacion_pct": round(ocupacion * 100, 1),
                "adr": dinero(adr),
                "tpv_por_pax": dinero(tpv_pax),
                "margen": dinero(margen),
                "brecha_diaria": dinero(brecha_diaria),
                "dias_negativos": int(mes["dias_negativos"] or 0),
                "contexto": contextos_mes(conn, corrida_id, mes["mes"]),
            }
            if margen < 0 and ocupacion < 0.70 and adr >= 95:
                insert_propuesta(conn, {
                    "corrida_id": corrida_id,
                    "centro_id": CENTRO_ID,
                    "mes": mes["mes"],
                    "area": "revenue",
                    "prioridad": "critica",
                    "titulo": "Lanzar campana de demanda sin bajar tarifa neta",
                    "diagnostico": f"El ADR medio es {adr:.2f} EUR, pero la ocupacion solo llega al {ocupacion*100:.1f}%. El problema principal no parece precio medio, sino falta de volumen rentable.",
                    "propuesta": "Crear campana segmentada para familias, repetidores y escapadas cortas: oferta con valor anadido controlado, cupo limitado, venta directa y paquetes con buffet/experiencia. Evitar descuentos limpios que bajen el ADR neto.",
                    "impacto_estimado": f"Objetivo: cubrir una brecha diaria aproximada de {brecha_diaria:.2f} EUR subiendo ocupacion sin deteriorar precio.",
                    "condicion_control": "Activar solo si la tarifa neta cubre coste variable, comision, coste fijo prorrateado y margen minimo.",
                    "soporte": soporte,
                })
                creadas += 1
            if margen < 0 and ocupacion >= 0.70:
                insert_propuesta(conn, {
                    "corrida_id": corrida_id,
                    "centro_id": CENTRO_ID,
                    "mes": mes["mes"],
                    "area": "pricing",
                    "prioridad": "critica",
                    "titulo": "Revisar precio minimo y cupos por canal",
                    "diagnostico": f"Con ocupacion {ocupacion*100:.1f}% el mes sigue perdiendo {abs(margen):.2f} EUR. Hay venta, pero no suficiente margen neto.",
                    "propuesta": "Calcular tarifa minima por canal incluyendo comision, regimen, coste de buffet y estructura. Cerrar cupos baratos en dias de alta demanda y mover disponibilidad a venta directa o canales con mejor neto.",
                    "impacto_estimado": "Evita llenar habitaciones que no pagan la estructura del hotel.",
                    "condicion_control": "Bloquear reservas cuyo neto por habitacion este por debajo del precio minimo mensual.",
                    "soporte": soporte,
                })
                creadas += 1
            if margen < 0 and tpv_pax < 9 and pax > 0:
                insert_propuesta(conn, {
                    "corrida_id": corrida_id,
                    "centro_id": CENTRO_ID,
                    "mes": mes["mes"],
                    "area": "bar_tpv",
                    "prioridad": "alta",
                    "titulo": "Subir gasto interno por cliente",
                    "diagnostico": f"El TPV por pax es {tpv_pax:.2f} EUR. Hay margen potencial en bar, piscina, combinados, cafes, snacks y experiencias.",
                    "propuesta": "Crear carta visible de cocteles, combinados, zumos naturales y packs piscina; formar al equipo para sugerencia activa sin presionar al cliente.",
                    "impacto_estimado": "Cada euro adicional por pax reduce la brecha mensual sin tocar precio de habitacion.",
                    "condicion_control": "Medir TPV por pax, ticket medio por punto de venta y satisfaccion cliente.",
                    "soporte": soporte,
                })
                creadas += 1
            if margen < 0 and float(mes["coste_fijo"] or 0) / max(float(mes["ingresos_total"] or 1), 1) > 0.18:
                insert_propuesta(conn, {
                    "corrida_id": corrida_id,
                    "centro_id": CENTRO_ID,
                    "mes": mes["mes"],
                    "area": "direccion",
                    "prioridad": "alta",
                    "titulo": "Coste fijo alto frente a ingreso",
                    "diagnostico": "La estructura fija pesa demasiado para el nivel de venta mensual.",
                    "propuesta": "Separar costes no negociables de costes ajustables: energia, agua, contratos, mantenimiento y software. Buscar ahorro sin recortar experiencia del cliente.",
                    "impacto_estimado": "Bajar punto muerto diario y hacer rentable la misma ocupacion.",
                    "condicion_control": "Comparar ahorro con impacto en satisfaccion y mantenimiento preventivo.",
                    "soporte": soporte,
                })
                creadas += 1
        conn.commit()
        print(f"Propuestas IA mensuales generadas/actualizadas: {creadas}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

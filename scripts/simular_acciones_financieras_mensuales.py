# -*- coding: utf-8 -*-
"""Simula acciones financieras mensuales sobre la vida operativa.

No modifica reservas ni costes reales. Crea escenarios de decision para ver si
una propuesta tapa la brecha o si solo desplaza el problema.
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
    parser = argparse.ArgumentParser(description="Simular acciones financieras mensuales.")
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


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_finanzas_acciones_mensuales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            mes TEXT,
            accion TEXT,
            descripcion TEXT,
            ingresos_estimados REAL DEFAULT 0,
            costes_estimados REAL DEFAULT 0,
            margen_estimado REAL DEFAULT 0,
            mejora_margen REAL DEFAULT 0,
            riesgo TEXT,
            veredicto TEXT,
            condicion_control TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes, accion)
        )
        """
    )


def money(value: float) -> float:
    return round(float(value or 0), 2)


def accion_resultado(base: sqlite3.Row, accion: str, descripcion: str, ingreso_delta: float, coste_delta: float, condicion: str, riesgo: str) -> dict:
    ingresos = float(base["ingresos_total"] or 0) + ingreso_delta
    costes = float(base["coste_total"] or 0) + coste_delta
    margen = ingresos - costes
    mejora = margen - float(base["margen"] or 0)
    if margen > 0 and mejora > 0:
        veredicto = "probar_controlado"
    elif mejora > 0:
        veredicto = "mejora_pero_no_basta"
    else:
        veredicto = "no_recomendado"
    return {
        "accion": accion,
        "descripcion": descripcion,
        "ingresos_estimados": money(ingresos),
        "costes_estimados": money(costes),
        "margen_estimado": money(margen),
        "mejora_margen": money(mejora),
        "riesgo": riesgo,
        "veredicto": veredicto,
        "condicion_control": condicion,
    }


def generar_acciones(row: sqlite3.Row, capacidad: int) -> list[dict]:
    dias = max(int(row["dias_simulados"] or 1), 1)
    habitaciones = float(row["habitaciones_vendidas"] or 0)
    pax = float(row["pax"] or 0)
    ingresos_hab = float(row["ingresos_hab"] or 0)
    ingresos_tpv = float(row["ingresos_tpv"] or 0)
    coste_laboral = float(row["coste_laboral"] or 0)
    coste_fijo = float(row["coste_fijo"] or 0)
    adr = ingresos_hab / habitaciones if habitaciones else float(row["precio_medio_habitacion"] or 0)
    ocupacion = habitaciones / max(capacidad * dias, 1)
    habitaciones_max = capacidad * dias
    libres = max(0, habitaciones_max - habitaciones)
    acciones: list[dict] = []

    # Campana con valor anadido: mas ocupacion, pequeno coste variable extra.
    extra_habs = min(libres, habitaciones_max * 0.08)
    ingreso_extra = extra_habs * max(adr * 0.94, 80)
    coste_extra = extra_habs * 18
    acciones.append(
        accion_resultado(
            row,
            "campana_valor_anadido",
            "Subir ocupacion hasta 8 puntos con venta directa y valor anadido, sin descuento limpio.",
            ingreso_extra,
            coste_extra,
            "Cupo limitado; tarifa neta por encima de coste variable + comision + margen minimo.",
            "medio" if ocupacion < 0.70 else "alto",
        )
    )

    # Tarifa minima: mejora ADR en habitaciones ya vendidas, con riesgo de demanda.
    ingreso_extra = habitaciones * adr * 0.04
    acciones.append(
        accion_resultado(
            row,
            "tarifa_minima_canal",
            "Aplicar tarifa minima neta por canal y cerrar cupos baratos.",
            ingreso_extra,
            0,
            "Solo en fechas con demanda o canales que no aportan cliente rentable.",
            "medio",
        )
    )

    # TPV interno: subir gasto por pax.
    ingreso_extra = pax * 2.0
    coste_extra = ingreso_extra * 0.28
    acciones.append(
        accion_resultado(
            row,
            "subir_tpv_pax",
            "Mejorar carta, cocteles, snack, cafes y venta sugerida suave.",
            ingreso_extra,
            coste_extra,
            "Medir ticket medio, quejas y satisfaccion; parar si se percibe presion comercial.",
            "bajo",
        )
    )

    # Flex laboral controlada: no despide, adapta refuerzos a ocupacion.
    ahorro = coste_laboral * (0.06 if ocupacion < 0.65 else 0.025)
    acciones.append(
        accion_resultado(
            row,
            "dimensionar_refuerzos",
            "Ajustar refuerzos por ocupacion real y salidas/llegadas, manteniendo servicio base.",
            0,
            -ahorro,
            "No bajar cobertura minima de pisos, recepcion, buffet ni mantenimiento.",
            "alto" if ocupacion > 0.85 else "medio",
        )
    )

    # Energia/contratos: pequeno ahorro fijo.
    ahorro = coste_fijo * 0.05
    acciones.append(
        accion_resultado(
            row,
            "revisar_costes_fijos",
            "Optimizar energia, agua, seguros, contratos e impuestos recurrentes donde sea posible.",
            0,
            -ahorro,
            "No tocar mantenimiento preventivo ni seguridad.",
            "bajo",
        )
    )

    # Combinado prudente.
    combinado_ingreso = min(libres, habitaciones_max * 0.05) * max(adr * 0.95, 80) + pax * 1.2
    combinado_coste = min(libres, habitaciones_max * 0.05) * 18 + (pax * 1.2) * 0.28 - (coste_laboral * 0.025) - (coste_fijo * 0.025)
    acciones.append(
        accion_resultado(
            row,
            "plan_combinado_prudente",
            "Combinar ocupacion controlada, TPV por pax y ajuste de refuerzos sin deteriorar experiencia.",
            combinado_ingreso,
            combinado_coste,
            "Ejecutar por semana y comparar contra forecast; si satisfaccion baja, cancelar.",
            "medio",
        )
    )

    return sorted(acciones, key=lambda x: (x["veredicto"] != "probar_controlado", -x["mejora_margen"]))


def capacidad_habitaciones(conn: sqlite3.Connection, centro_id: int) -> int:
    if not table_exists(conn, "habitaciones_recepcion"):
        return 160
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(habitaciones_recepcion)")}
    activa_clause = "AND COALESCE(activa,1)=1" if "activa" in cols else "AND COALESCE(activo,1)=1" if "activo" in cols else ""
    row = conn.execute(
        f"SELECT COUNT(*) total FROM habitaciones_recepcion WHERE empresa_id=? AND centro_id=? {activa_clause}",
        (EMPRESA_ID, centro_id),
    ).fetchone()
    return int(row["total"] or 0) or 160


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
            raise SystemExit("No hay corrida para simular acciones.")
        capacidad = capacidad_habitaciones(conn, args.centro_id)
        rows = conn.execute(
            """
            SELECT rm.*,
                   COALESCE(SUM(k.habitaciones_ocupadas), 0) habitaciones_vendidas,
                   COALESCE(SUM(k.pax), 0) pax,
                   COALESCE(SUM(k.ingresos_habitaciones), 0) ingresos_hab,
                   COALESCE(SUM(k.ingresos_tpv), 0) ingresos_tpv
            FROM simulador_resultado_mensual rm
            LEFT JOIN simulador_vida_kpis_diarios k
              ON k.corrida_id=rm.corrida_id
             AND k.centro_id=rm.centro_id
             AND substr(k.fecha,1,7)=rm.mes
            WHERE rm.corrida_id=? AND rm.centro_id=?
            GROUP BY rm.id
            ORDER BY rm.mes
            """,
            (corrida_id, args.centro_id),
        ).fetchall()
        conn.execute(
            "DELETE FROM simulador_finanzas_acciones_mensuales WHERE corrida_id=? AND centro_id=?",
            (corrida_id, args.centro_id),
        )
        total = 0
        for row in rows:
            acciones = generar_acciones(row, capacidad)
            for accion in acciones:
                conn.execute(
                    """
                    INSERT INTO simulador_finanzas_acciones_mensuales
                    (corrida_id, centro_id, mes, accion, descripcion, ingresos_estimados,
                     costes_estimados, margen_estimado, mejora_margen, riesgo, veredicto,
                     condicion_control, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        corrida_id,
                        args.centro_id,
                        row["mes"],
                        accion["accion"],
                        accion["descripcion"],
                        accion["ingresos_estimados"],
                        accion["costes_estimados"],
                        accion["margen_estimado"],
                        accion["mejora_margen"],
                        accion["riesgo"],
                        accion["veredicto"],
                        accion["condicion_control"],
                    ),
                )
                total += 1
        conn.commit()
        print({"corrida_id": corrida_id, "acciones_simuladas": total})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

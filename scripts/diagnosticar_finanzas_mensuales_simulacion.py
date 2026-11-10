# -*- coding: utf-8 -*-
"""Diagnostico financiero mensual para el simulador Hotel 1.

No modifica operativa. Cruza resultado mensual, KPIs diarios y contexto externo
para explicar por que un mes gana o pierde y que experimento probar.
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
    parser = argparse.ArgumentParser(description="Diagnosticar finanzas mensuales de la simulacion.")
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
        CREATE TABLE IF NOT EXISTS simulador_finanzas_diagnostico_mensual (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            mes TEXT,
            estado TEXT,
            causa_principal TEXT,
            causa_secundaria TEXT,
            lectura TEXT,
            ocupacion_pct REAL DEFAULT 0,
            adr REAL DEFAULT 0,
            revpar REAL DEFAULT 0,
            tpv_pax REAL DEFAULT 0,
            coste_laboral_pct REAL DEFAULT 0,
            coste_compras_pct REAL DEFAULT 0,
            coste_fijo_pct REAL DEFAULT 0,
            margen REAL DEFAULT 0,
            brecha_diaria REAL DEFAULT 0,
            experimento_recomendado TEXT,
            condicion_no_arruinarse TEXT,
            soporte_json TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes)
        )
        """
    )


def money(value: float) -> float:
    return round(float(value or 0), 2)


def pct(value: float) -> float:
    return round(float(value or 0) * 100, 2)


def capacidad_habitaciones(conn: sqlite3.Connection, centro_id: int) -> int:
    if not table_exists(conn, "habitaciones_recepcion"):
        return 160
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(habitaciones_recepcion)")}
    activa_clause = "AND COALESCE(activa,1)=1" if "activa" in cols else "AND COALESCE(activo,1)=1" if "activo" in cols else ""
    row = conn.execute(
        f"""
        SELECT COUNT(*) total
        FROM habitaciones_recepcion
        WHERE empresa_id=? AND centro_id=? {activa_clause}
        """,
        (EMPRESA_ID, centro_id),
    ).fetchone()
    return int(row["total"] or 0) or 160


def contextos(conn: sqlite3.Connection, corrida_id: int, centro_id: int, mes: str) -> list[dict]:
    if not table_exists(conn, "simulador_contexto_externo"):
        return []
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT tipo, titulo, intensidad, descripcion
            FROM simulador_contexto_externo
            WHERE corrida_id=? AND centro_id=?
              AND substr(fecha_inicio,1,7) <= ?
              AND substr(fecha_fin,1,7) >= ?
            ORDER BY intensidad DESC
            """,
            (corrida_id, centro_id, mes, mes),
        ).fetchall()
    ]


def diagnosticar(row: sqlite3.Row, capacidad: int, ctx: list[dict]) -> dict:
    dias = max(int(row["dias_simulados"] or 1), 1)
    habitaciones = float(row["habitaciones_vendidas"] or 0)
    pax = float(row["pax"] or 0)
    ingresos = float(row["ingresos_total"] or 0)
    ingresos_hab = float(row["ingresos_hab"] or 0)
    ingresos_tpv = float(row["ingresos_tpv"] or 0)
    coste_laboral = float(row["coste_laboral"] or 0)
    coste_compras = float(row["coste_compras"] or 0)
    coste_fijo = float(row["coste_fijo"] or 0)
    coste_total = float(row["coste_total"] or 0)
    margen = float(row["margen"] or 0)
    ocupacion = habitaciones / max(capacidad * dias, 1)
    adr = ingresos_hab / habitaciones if habitaciones else 0
    revpar = ingresos_hab / max(capacidad * dias, 1)
    tpv_pax = ingresos_tpv / pax if pax else 0
    brecha_diaria = max((coste_total - ingresos) / dias, 0)
    laboral_pct = coste_laboral / max(ingresos, 1)
    compras_pct = coste_compras / max(ingresos, 1)
    fijo_pct = coste_fijo / max(ingresos, 1)

    causa = "rentable"
    secundaria = "controlar_calidad"
    experimento = "Mantener estrategia y usar el mes como referencia para comparar meses debiles."
    condicion = "No subir costes fijos si el incremento de ingreso no esta confirmado."
    if margen < 0:
        if ocupacion < 0.58:
            causa = "ocupacion_insuficiente"
            secundaria = "demanda_o_distribucion"
            experimento = "Lanzar campana limitada por segmento y canal directo, con valor anadido en vez de descuento limpio."
            condicion = "Aceptar solo reservas cuyo neto cubra coste variable, comision, limpieza, buffet y margen minimo."
        elif adr < 105:
            causa = "precio_neto_bajo"
            secundaria = "mix_canal_o_cupo_barato"
            experimento = "Cerrar cupos baratos en dias de demanda y definir tarifa minima neta por canal."
            condicion = "No llenar habitaciones por debajo del punto muerto por habitacion disponible."
        elif laboral_pct > 0.48:
            causa = "estructura_laboral_pesada"
            secundaria = "dimensionamiento_turnos"
            experimento = "Simular plantilla base y refuerzos por ocupacion antes de publicar cuadrantes."
            condicion = "No recortar por debajo del nivel que afecte limpieza, buffet, recepcion y satisfaccion."
        elif fijo_pct > 0.18:
            causa = "coste_fijo_alto"
            secundaria = "energia_contratos_ibi_seguros"
            experimento = "Separar gastos no negociables de optimizables y crear objetivo de ahorro por contrato."
            condicion = "Ahorro solo valido si no empeora mantenimiento preventivo ni experiencia cliente."
        elif tpv_pax < 7:
            causa = "bajo_gasto_interno"
            secundaria = "bar_piscina_y_experiencias"
            experimento = "Probar packs bar-piscina, cocteles, cafes, snacks y venta sugerida suave."
            condicion = "Medir ticket medio y quejas; no empujar venta si baja satisfaccion."
        else:
            causa = "margen_no_cubre_estructura"
            secundaria = "mix_de_varios_factores"
            experimento = "Probar escenario combinado: subir ocupacion 5 puntos, TPV por pax 2 EUR y revisar coste fijo."
            condicion = "Ejecutar cambios por lotes pequenos y comparar contra grupo de control."

    if ctx and margen < 0:
        secundaria = f"{secundaria}+contexto_externo"
        experimento += " Cruzar la accion con contexto externo activo antes de abrir cupos."

    estado = "perdida" if margen < 0 else "rentable"
    lectura = (
        f"{row['mes']}: {estado}. Ocupacion {ocupacion*100:.1f}%, ADR {adr:.2f}, "
        f"RevPAR {revpar:.2f}, margen {margen:.2f}. Causa: {causa}."
    )
    return {
        "estado": estado,
        "causa_principal": causa,
        "causa_secundaria": secundaria,
        "lectura": lectura,
        "ocupacion_pct": pct(ocupacion),
        "adr": money(adr),
        "revpar": money(revpar),
        "tpv_pax": money(tpv_pax),
        "coste_laboral_pct": pct(laboral_pct),
        "coste_compras_pct": pct(compras_pct),
        "coste_fijo_pct": pct(fijo_pct),
        "margen": money(margen),
        "brecha_diaria": money(brecha_diaria),
        "experimento_recomendado": experimento,
        "condicion_no_arruinarse": condicion,
        "soporte": {
            "dias_negativos": int(row["dias_negativos"] or 0),
            "ingresos": money(ingresos),
            "coste_total": money(coste_total),
            "habitaciones_vendidas": int(habitaciones),
            "pax": int(pax),
            "contexto_externo": ctx,
        },
    }


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
            raise SystemExit("No hay corrida para diagnosticar.")
        capacidad = capacidad_habitaciones(conn, args.centro_id)
        meses = conn.execute(
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
            "DELETE FROM simulador_finanzas_diagnostico_mensual WHERE corrida_id=? AND centro_id=?",
            (corrida_id, args.centro_id),
        )
        for mes in meses:
            ctx = contextos(conn, corrida_id, args.centro_id, mes["mes"])
            d = diagnosticar(mes, capacidad, ctx)
            conn.execute(
                """
                INSERT INTO simulador_finanzas_diagnostico_mensual
                (corrida_id, centro_id, mes, estado, causa_principal, causa_secundaria,
                 lectura, ocupacion_pct, adr, revpar, tpv_pax, coste_laboral_pct,
                 coste_compras_pct, coste_fijo_pct, margen, brecha_diaria,
                 experimento_recomendado, condicion_no_arruinarse, soporte_json, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    corrida_id,
                    args.centro_id,
                    mes["mes"],
                    d["estado"],
                    d["causa_principal"],
                    d["causa_secundaria"],
                    d["lectura"],
                    d["ocupacion_pct"],
                    d["adr"],
                    d["revpar"],
                    d["tpv_pax"],
                    d["coste_laboral_pct"],
                    d["coste_compras_pct"],
                    d["coste_fijo_pct"],
                    d["margen"],
                    d["brecha_diaria"],
                    d["experimento_recomendado"],
                    d["condicion_no_arruinarse"],
                    json.dumps(d["soporte"], ensure_ascii=False),
                ),
            )
        conn.commit()
        print({"corrida_id": corrida_id, "meses_diagnosticados": len(meses)})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

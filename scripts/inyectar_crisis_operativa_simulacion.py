# -*- coding: utf-8 -*-
"""Inyecta crisis cruzadas en la simulacion sin tocar datos reales.

El objetivo no es romper por romper, sino crear situaciones encadenadas:
proveedor falla, cocina improvisa, APPCC se tensiona, cliente se queja,
mantenimiento o RRHH afectan la operacion y el cerebro debe explicarlo.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


CRISIS = [
    {
        "tipo": "proveedor_entrega_parcial",
        "modulos": ("compras", "cocina", "stock"),
        "severidad": "alta",
        "titulo": "Proveedor entrega parcial de producto critico",
        "descripcion": "Falta producto principal para menu; cocina debe cambiar mise en place y compras debe activar proveedor alternativo.",
        "impacto": {"coste_compras": 380, "alertas": 4, "riesgo": 10, "quejas": 1},
        "accion": "Activar proveedor alternativo, bloquear receta afectada y recalcular menu antes del servicio.",
    },
    {
        "tipo": "baja_equipo_pisos",
        "modulos": ("rrhh", "pisos", "recepcion"),
        "severidad": "alta",
        "titulo": "Baja imprevista en pisos con salida masiva",
        "descripcion": "Dos camareras de pisos no se presentan en dia de salidas; recepcion puede retrasar check-in y subir quejas.",
        "impacto": {"coste_laboral": 240, "alertas": 5, "riesgo": 14, "quejas": 3},
        "accion": "Reasignar rotativo, priorizar habitaciones de llegada y avisar recepcion de retrasos previstos.",
    },
    {
        "tipo": "averia_habitaciones_clima",
        "modulos": ("mantenimiento", "recepcion", "experiencia"),
        "severidad": "critica",
        "titulo": "Averia de fan-coil en habitaciones vendidas",
        "descripcion": "Climatizacion falla en varias habitaciones ocupadas; posible compensacion y cambio de habitacion.",
        "impacto": {"coste_fijo": 180, "alertas": 4, "riesgo": 18, "quejas": 4},
        "accion": "Abrir orden urgente, bloquear habitaciones afectadas y preparar compensacion controlada.",
    },
    {
        "tipo": "appcc_agua_piscina",
        "modulos": ("appcc", "mantenimiento", "direccion"),
        "severidad": "critica",
        "titulo": "Control de agua/piscina fuera de rango",
        "descripcion": "Parametro APPCC fuera de rango; toca cerrar uso, registrar correccion y comunicar internamente.",
        "impacto": {"coste_fijo": 120, "alertas": 6, "riesgo": 22, "appcc": 2, "quejas": 2},
        "accion": "Bloquear zona, registrar medida correctiva, repetir medicion y no reabrir sin validacion.",
    },
    {
        "tipo": "pico_checkin_grupo",
        "modulos": ("recepcion", "sala", "cocina"),
        "severidad": "media",
        "titulo": "Llegada de grupo concentrada antes de cena",
        "descripcion": "Grupo llega tarde y concentrado; recepcion, buffet y sala se tensionan al mismo tiempo.",
        "impacto": {"coste_laboral": 160, "alertas": 3, "riesgo": 8, "quejas": 1, "ingresos_tpv": 90},
        "accion": "Refuerzo corto en recepcion, preasignar habitaciones y retrasar reposicion caliente controlada.",
    },
    {
        "tipo": "descuadre_tpv_caja",
        "modulos": ("tpv", "contabilidad", "sala"),
        "severidad": "alta",
        "titulo": "Descuadre de caja en punto de venta",
        "descripcion": "Cierre de caja no cuadra con cobros; contabilidad no debe aceptar cierre sin revision.",
        "impacto": {"alertas": 3, "riesgo": 9},
        "accion": "Bloquear cierre de caja, revisar tickets anulados, formas de pago y responsable de turno.",
    },
    {
        "tipo": "alergeno_buffet_dudoso",
        "modulos": ("cocina", "buffet", "appcc"),
        "severidad": "critica",
        "titulo": "Alergeno dudoso en plato expuesto",
        "descripcion": "Plato en buffet sin alergenos validados; debe retirarse o etiquetarse como pendiente hasta validar.",
        "impacto": {"alertas": 5, "riesgo": 24, "appcc": 1},
        "accion": "Retirar tarjeta/plato dudoso, validar ficha tecnica y registrar accion APPCC.",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inyectar crisis operativas cruzadas en simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--desde", default="", help="Fecha minima YYYY-MM-DD. Si se omite elige dias de la corrida.")
    parser.add_argument("--dias", type=int, default=10, help="Numero de dias con crisis.")
    parser.add_argument("--intensidad", choices=("media", "alta", "brutal"), default="alta")
    parser.add_argument("--seed", type=int, default=20260610)
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
        CREATE TABLE IF NOT EXISTS simulador_crisis_operativa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha TEXT,
            tipo TEXT,
            severidad TEXT,
            titulo TEXT,
            descripcion TEXT,
            accion_recomendada TEXT,
            modulos_json TEXT,
            impacto_json TEXT,
            estado TEXT DEFAULT 'pendiente',
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, fecha, tipo, centro_id)
        )
        """
    )


def choose_days(conn: sqlite3.Connection, corrida_id: int, desde: str, dias: int, centro_id: int) -> list[str]:
    where = ["corrida_id=?", "centro_id=?"]
    params: list[object] = [corrida_id, centro_id]
    if desde:
        where.append("fecha>=?")
        params.append(desde)
    rows = conn.execute(
        f"""
        SELECT fecha, habitaciones_ocupadas, pax, margen_estimado, riesgo_operativo
        FROM simulador_vida_kpis_diarios
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE WHEN margen_estimado < 0 THEN 0 ELSE 1 END,
            riesgo_operativo DESC,
            habitaciones_ocupadas DESC,
            fecha ASC
        LIMIT ?
        """,
        (*params, max(dias * 3, dias)),
    ).fetchall()
    fechas = [row["fecha"] for row in rows]
    if len(fechas) < dias and rows:
        start = date.fromisoformat(rows[-1]["fecha"])
        while len(fechas) < dias:
            start += timedelta(days=1)
            fechas.append(start.isoformat())
    return fechas[:dias]


def insert_event(conn: sqlite3.Connection, corrida_id: int, fecha: str, modulo: str, crisis_id: int, crisis: dict, impacto: dict, centro_id: int) -> None:
    conn.execute(
        """
        INSERT INTO simulador_vida_eventos (
            corrida_id, fecha_simulada, modulo, tipo, severidad, descripcion,
            tabla_ref, registro_id, impacto_json, empresa_id, centro_id
        ) VALUES (?, ?, ?, ?, ?, ?, 'simulador_crisis_operativa', ?, ?, 1, ?)
        """,
        (
            corrida_id,
            fecha,
            modulo,
            crisis["tipo"],
            crisis["severidad"],
            f"{crisis['titulo']}: {crisis['descripcion']}",
            crisis_id,
            json.dumps(impacto, ensure_ascii=False),
            centro_id,
        ),
    )


def update_kpi(conn: sqlite3.Connection, corrida_id: int, fecha: str, crisis: dict, factor: float, centro_id: int) -> None:
    impacto = crisis["impacto"]
    coste_laboral = float(impacto.get("coste_laboral", 0)) * factor
    coste_compras = float(impacto.get("coste_compras", 0)) * factor
    coste_fijo = float(impacto.get("coste_fijo", 0)) * factor
    ingresos_tpv = float(impacto.get("ingresos_tpv", 0)) * factor
    quejas = int(round(float(impacto.get("quejas", 0)) * factor))
    alertas = int(round(float(impacto.get("alertas", 0)) * factor))
    appcc = int(round(float(impacto.get("appcc", 0)) * factor))
    riesgo = float(impacto.get("riesgo", 0)) * factor
    conn.execute(
        """
        UPDATE simulador_vida_kpis_diarios
        SET coste_laboral_estimado = COALESCE(coste_laboral_estimado,0) + ?,
            coste_compras_estimado = COALESCE(coste_compras_estimado,0) + ?,
            coste_fijo_estimado = COALESCE(coste_fijo_estimado,0) + ?,
            coste_total_estimado = COALESCE(coste_total_estimado,0) + ?,
            ingresos_tpv = COALESCE(ingresos_tpv,0) + ?,
            quejas = COALESCE(quejas,0) + ?,
            alertas_altas = COALESCE(alertas_altas,0) + ?,
            no_conformidades_appcc = COALESCE(no_conformidades_appcc,0) + ?,
            riesgo_operativo = MIN(100, COALESCE(riesgo_operativo,0) + ?),
            margen_estimado = COALESCE(margen_estimado,0) + ? - ?,
            lectura = 'Dia tensionado por crisis operativa cruzada: revisar eventos simulados y plan de accion.'
        WHERE corrida_id=? AND fecha=? AND centro_id=?
        """,
        (
            coste_laboral,
            coste_compras,
            coste_fijo,
            coste_laboral + coste_compras + coste_fijo,
            ingresos_tpv,
            quejas,
            alertas,
            appcc,
            riesgo,
            ingresos_tpv,
            coste_laboral + coste_compras + coste_fijo,
            corrida_id,
            fecha,
            centro_id,
        ),
    )


def insert_plan(conn: sqlite3.Connection, corrida_id: int, crisis: dict, evidencia: dict, centro_id: int) -> None:
    if not table_exists(conn, "simulador_vida_plan_accion"):
        return
    exists = conn.execute(
        """
        SELECT id FROM simulador_vida_plan_accion
        WHERE corrida_id=? AND area=? AND titulo=? AND centro_id=?
        LIMIT 1
        """,
        (corrida_id, "+".join(crisis["modulos"]), crisis["titulo"], centro_id),
    ).fetchone()
    if exists:
        return
    conn.execute(
        """
        INSERT INTO simulador_vida_plan_accion (
            corrida_id, area, prioridad, titulo, problema, accion_recomendada,
            indicador_objetivo, impacto_estimado, evidencia_json, estado, empresa_id, centro_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', 1, ?)
        """,
        (
            corrida_id,
            "+".join(crisis["modulos"]),
            crisis["severidad"],
            crisis["titulo"],
            crisis["descripcion"],
            crisis["accion"],
            "Crisis simulada cerrada con responsable antes del siguiente servicio",
            "Reduce quejas, riesgo APPCC y decisiones improvisadas en recepcion/cocina.",
            json.dumps(evidencia, ensure_ascii=False),
            centro_id,
        ),
    )


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    db = Path(args.db)
    if not db.is_absolute():
        db = ROOT / db
    if not db.exists():
        raise SystemExit(f"No existe la base: {db}")
    conn = connect(db)
    try:
        ensure_schema(conn)
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida de simulacion.")
        factor = {"media": 0.7, "alta": 1.0, "brutal": 1.45}[args.intensidad]
        fechas = choose_days(conn, corrida_id, args.desde, args.dias, args.centro_id)
        creadas = 0
        eventos = 0
        for idx, fecha in enumerate(fechas):
            crisis = CRISIS[idx % len(CRISIS)]
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO simulador_crisis_operativa (
                    corrida_id, fecha, tipo, severidad, titulo, descripcion,
                    accion_recomendada, modulos_json, impacto_json, empresa_id, centro_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    corrida_id,
                    fecha,
                    crisis["tipo"],
                    crisis["severidad"],
                    crisis["titulo"],
                    crisis["descripcion"],
                    crisis["accion"],
                    json.dumps(crisis["modulos"], ensure_ascii=False),
                    json.dumps({"base": crisis["impacto"], "factor": factor}, ensure_ascii=False),
                    args.centro_id,
                ),
            )
            if cur.rowcount <= 0:
                continue
            crisis_id = int(cur.lastrowid)
            creadas += 1
            impacto = {"base": crisis["impacto"], "factor": factor, "accion": crisis["accion"]}
            for modulo in crisis["modulos"]:
                insert_event(conn, corrida_id, fecha, modulo, crisis_id, crisis, impacto, args.centro_id)
                eventos += 1
            update_kpi(conn, corrida_id, fecha, crisis, factor, args.centro_id)
            insert_plan(conn, corrida_id, crisis, {"fecha": fecha, "crisis_id": crisis_id, "impacto": impacto}, args.centro_id)
        conn.commit()
        print(json.dumps({
            "ok": True,
            "db": str(db),
            "corrida_id": corrida_id,
            "centro_id": args.centro_id,
            "intensidad": args.intensidad,
            "crisis_creadas": creadas,
            "eventos_creados": eventos,
            "fechas": fechas,
        }, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

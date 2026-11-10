# -*- coding: utf-8 -*-
"""Diagnostica dias malos de la simulacion de vida operativa.

No borra ni regenera datos. Lee la ultima corrida y crea diagnosticos por dia
para entender por que se pierde dinero o sube el riesgo operativo.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnosticar KPIs diarios de la simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_vida_diagnosticos_dia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha TEXT,
            nivel TEXT,
            titulo TEXT,
            diagnostico TEXT,
            causas_json TEXT,
            acciones_json TEXT,
            margen_estimado REAL,
            riesgo_operativo REAL,
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_vida_plan_accion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            area TEXT,
            prioridad TEXT,
            titulo TEXT,
            problema TEXT,
            accion_recomendada TEXT,
            indicador_objetivo TEXT,
            impacto_estimado TEXT,
            evidencia_json TEXT,
            estado TEXT DEFAULT 'pendiente',
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def latest_corrida(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def events_for_day(conn: sqlite3.Connection, corrida_id: int, fecha: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT modulo, tipo, severidad, descripcion
        FROM simulador_vida_eventos
        WHERE corrida_id=? AND fecha_simulada=? AND severidad IN ('alta','critica','media')
        ORDER BY CASE severidad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END, modulo
        LIMIT 30
        """,
        (corrida_id, fecha),
    ).fetchall()


def context_for_day(conn: sqlite3.Connection, corrida_id: int, fecha: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT tipo, titulo, intensidad, descripcion
        FROM simulador_contexto_externo
        WHERE corrida_id=? AND fecha_inicio <= ? AND fecha_fin > ?
        ORDER BY intensidad DESC
        """,
        (corrida_id, fecha, fecha),
    ).fetchall()


def build_diagnostic(conn: sqlite3.Connection, row: sqlite3.Row) -> dict | None:
    margen = float(row["margen_estimado"] or 0)
    riesgo = float(row["riesgo_operativo"] or 0)
    if margen >= 0 and riesgo < 70:
        return None

    causas = []
    acciones = []
    ingresos = float(row["ingresos_habitaciones"] or 0) + float(row["ingresos_tpv"] or 0)
    laboral = float(row["coste_laboral_estimado"] or 0)
    compras = float(row["coste_compras_estimado"] or 0)
    fijo = float(row["coste_fijo_estimado"] if "coste_fijo_estimado" in row.keys() else 0 or 0)
    hab = int(row["habitaciones_ocupadas"] or 0)
    pax = int(row["pax"] or 0)

    if ingresos and laboral / ingresos > 0.58:
        causas.append({
            "causa": "coste_laboral_alto",
            "dato": round(laboral / ingresos * 100, 1),
            "lectura": "El coste laboral pesa demasiado frente al ingreso del dia.",
        })
        acciones.append("Revisar cuadrante del dia, turnos partidos y refuerzos frente a ocupacion real.")
    if ingresos and compras / ingresos > 0.32:
        causas.append({
            "causa": "compras_altas",
            "dato": round(compras / ingresos * 100, 1),
            "lectura": "Las compras imputadas al dia presionan el margen.",
        })
        acciones.append("Separar pedido de reposicion de consumo real y revisar proveedor/productos caros.")
    if ingresos and fijo / ingresos > 0.18:
        causas.append({
            "causa": "costes_fijos_estructura",
            "dato": round(fijo / ingresos * 100, 1),
            "lectura": "Luz, agua, seguros, IBI y contratos fijos pesan demasiado frente a la venta del dia.",
        })
        acciones.append("Calcular punto muerto diario y precio minimo neto antes de abrir cupos baratos.")
    if hab < 80 and ingresos < 9000:
        causas.append({
            "causa": "ocupacion_baja",
            "dato": hab,
            "lectura": "La ocupacion no cubre estructura fija del hotel.",
        })
        acciones.append("Revisar precio, canales, cupos y oferta para los dias equivalentes.")
    if int(row["quejas"] or 0) > 0:
        causas.append({
            "causa": "quejas_cliente",
            "dato": int(row["quejas"] or 0),
            "lectura": "Hay quejas que pueden generar compensaciones o perdida de repeticion.",
        })
        acciones.append("Cruzar quejas con departamento, turno, ocupacion y responsable.")
    if int(row["no_conformidades_appcc"] or 0) > 0:
        causas.append({
            "causa": "appcc",
            "dato": int(row["no_conformidades_appcc"] or 0),
            "lectura": "APPCC no conforme obliga a corregir antes de vender tranquilidad.",
        })
        acciones.append("Bloquear cierre operativo hasta registrar accion correctiva APPCC.")
    if int(row["ordenes_mantenimiento"] or 0) >= 2:
        causas.append({
            "causa": "mantenimiento",
            "dato": int(row["ordenes_mantenimiento"] or 0),
            "lectura": "Mantenimiento tensiona habitaciones, experiencia o activos criticos.",
        })
        acciones.append("Revisar si las ordenes afectan habitaciones vendibles, piscina, buffet o climatizacion.")

    eventos = events_for_day(conn, int(row["corrida_id"]), row["fecha"])
    if eventos:
        causas.append({
            "causa": "alertas_operativas",
            "dato": len(eventos),
            "lectura": "El dia acumula alertas operativas relevantes.",
            "eventos": [dict(e) for e in eventos[:8]],
        })
        acciones.append("Revisar alertas del dia por modulo antes de repetir la misma operacion.")

    contexto = context_for_day(conn, int(row["corrida_id"]), row["fecha"])
    if contexto:
        causas.append({
            "causa": "contexto_externo",
            "dato": len(contexto),
            "lectura": "Hay senales externas activas que pueden explicar presion comercial u operativa.",
            "contexto": [dict(c) for c in contexto],
        })
        acciones.append("Cruzar contexto externo con precio, cancelaciones, compras y quejas antes de decidir.")

    if not causas:
        causas.append({
            "causa": "margen_modelo",
            "dato": margen,
            "lectura": "El margen cae sin una causa dominante: revisar formula o datos base.",
        })
        acciones.append("Auditar formula del KPI y trazabilidad de ingresos/costes del dia.")

    nivel = "critica" if margen < 0 or riesgo >= 85 else "alta"
    titulo = "Dia con perdida estimada" if margen < 0 else "Dia con riesgo operativo alto"
    diagnostico = (
        f"{row['fecha']}: margen {margen:.2f} EUR, riesgo {riesgo:.1f}, "
        f"{hab} habitaciones, {pax} pax. {row['lectura']}"
    )
    return {
        "corrida_id": int(row["corrida_id"]),
        "fecha": row["fecha"],
        "nivel": nivel,
        "titulo": titulo,
        "diagnostico": diagnostico,
        "causas": causas,
        "acciones": acciones,
        "margen_estimado": margen,
        "riesgo_operativo": riesgo,
        "empresa_id": int(row["empresa_id"] or 1),
        "centro_id": int(row["centro_id"] or 3),
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
            raise SystemExit("No hay corrida de simulacion.")
        rows = conn.execute(
            """
            SELECT *
            FROM simulador_vida_kpis_diarios
            WHERE corrida_id=? AND (margen_estimado < 0 OR riesgo_operativo >= 70)
            ORDER BY margen_estimado ASC, riesgo_operativo DESC
            """,
            (corrida_id,),
        ).fetchall()
        creados = 0
        for row in rows:
            diag = build_diagnostic(conn, row)
            if not diag:
                continue
            existe = conn.execute(
                """
                SELECT id FROM simulador_vida_diagnosticos_dia
                WHERE corrida_id=? AND fecha=? AND titulo=?
                LIMIT 1
                """,
                (diag["corrida_id"], diag["fecha"], diag["titulo"]),
            ).fetchone()
            values = (
                diag["nivel"],
                diag["diagnostico"],
                json.dumps(diag["causas"], ensure_ascii=False),
                json.dumps(diag["acciones"], ensure_ascii=False),
                diag["margen_estimado"],
                diag["riesgo_operativo"],
                diag["empresa_id"],
                diag["centro_id"],
            )
            if existe:
                conn.execute(
                    """
                    UPDATE simulador_vida_diagnosticos_dia
                    SET nivel=?, diagnostico=?, causas_json=?, acciones_json=?,
                        margen_estimado=?, riesgo_operativo=?, empresa_id=?, centro_id=?
                    WHERE id=?
                    """,
                    values + (existe["id"],),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO simulador_vida_diagnosticos_dia
                    (corrida_id, fecha, nivel, titulo, diagnostico, causas_json, acciones_json,
                     margen_estimado, riesgo_operativo, empresa_id, centro_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        diag["corrida_id"],
                        diag["fecha"],
                        diag["nivel"],
                        diag["titulo"],
                        diag["diagnostico"],
                        json.dumps(diag["causas"], ensure_ascii=False),
                        json.dumps(diag["acciones"], ensure_ascii=False),
                        diag["margen_estimado"],
                        diag["riesgo_operativo"],
                        diag["empresa_id"],
                        diag["centro_id"],
                    ),
                )
                creados += 1
        planes = generate_action_plan(conn, corrida_id)
        conn.commit()
        print(f"Diagnosticos creados/actualizados para corrida {corrida_id}: {len(rows)} revisados, {creados} nuevos. Planes: {planes}.")
    finally:
        conn.close()


def _load_json_list(text: str | None) -> list:
    if not text:
        return []
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def generate_action_plan(conn: sqlite3.Connection, corrida_id: int) -> int:
    conn.execute("DELETE FROM simulador_vida_plan_accion WHERE corrida_id=?", (corrida_id,))
    rows = conn.execute(
        """
        SELECT *
        FROM simulador_vida_diagnosticos_dia
        WHERE corrida_id=?
        """,
        (corrida_id,),
    ).fetchall()
    counters: dict[str, dict] = {}
    for row in rows:
        for causa in _load_json_list(row["causas_json"]):
            key = causa.get("causa") or "sin_clasificar"
            item = counters.setdefault(
                key,
                {
                    "dias": 0,
                    "margen": 0.0,
                    "riesgo": 0.0,
                    "ejemplos": [],
                },
            )
            item["dias"] += 1
            item["margen"] += float(row["margen_estimado"] or 0)
            item["riesgo"] += float(row["riesgo_operativo"] or 0)
            if len(item["ejemplos"]) < 5:
                item["ejemplos"].append({
                    "fecha": row["fecha"],
                    "margen": row["margen_estimado"],
                    "riesgo": row["riesgo_operativo"],
                    "lectura": causa.get("lectura"),
                    "dato": causa.get("dato"),
                })
    if table_exists(conn, "simulador_overbooking_casos"):
        ob_rows = conn.execute(
            """
            SELECT tipo, severidad, estado_gestion, fecha_entrada, coste_estimado
            FROM simulador_overbooking_casos
            WHERE corrida_id=?
            """,
            (corrida_id,),
        ).fetchall()
        if ob_rows:
            item = counters.setdefault("overbooking", {"dias": 0, "margen": 0.0, "riesgo": 0.0, "ejemplos": []})
            item["dias"] = len(ob_rows)
            item["margen"] = -sum(float(r["coste_estimado"] or 0) for r in ob_rows)
            item["riesgo"] = sum(90 if r["severidad"] == "critica" else 65 for r in ob_rows)
            for r in ob_rows[:5]:
                item["ejemplos"].append({
                    "fecha": r["fecha_entrada"],
                    "margen": -float(r["coste_estimado"] or 0),
                    "riesgo": 90 if r["severidad"] == "critica" else 65,
                    "lectura": f"{r['tipo']} {r['estado_gestion']}",
                    "dato": r["coste_estimado"],
                })

    definitions = {
        "coste_laboral_alto": {
            "area": "rrhh",
            "titulo": "Ajustar plantilla a ocupacion real",
            "accion": "Crear regla de dimensionamiento por ocupacion: plantilla base, refuerzos por pax y bloqueo de turnos extra si no hay ingreso previsto.",
            "indicador": "Coste laboral / ingresos diarios por debajo del 45%",
            "impacto": "Mejora margen sin recortar servicio en dias de alta ocupacion.",
        },
        "compras_altas": {
            "area": "compras",
            "titulo": "Separar compra de reposicion y consumo real",
            "accion": "Marcar pedidos automaticos como reposicion, imputar coste por consumo/lote y revisar proveedor cuando el dia supera umbral.",
            "indicador": "Compras imputadas / ingresos por debajo del 28%",
            "impacto": "Evita falsos dias malos y descubre productos o proveedores que erosionan margen.",
        },
        "costes_fijos_estructura": {
            "area": "direccion",
            "titulo": "Meter estructura fija en precios y cupos",
            "accion": "Recalcular punto muerto por mes con luz, agua, seguros, IBI, tasas y contratos; bloquear tarifas netas que no cubran coste total previsto.",
            "indicador": "Margen mensual positivo despues de costes fijos",
            "impacto": "Evita vender ocupacion que llena el hotel pero no paga la estructura.",
        },
        "ocupacion_baja": {
            "area": "revenue",
            "titulo": "Dias de baja ocupacion necesitan estrategia propia",
            "accion": "Activar paquete de demanda, revisar cupos, canales, precio minimo y coste operativo reducido para dias flojos.",
            "indicador": "Habitaciones ocupadas por encima del punto muerto operativo",
            "impacto": "Reduce dias negativos y evita vender barato sin cubrir estructura.",
        },
        "alertas_operativas": {
            "area": "operaciones",
            "titulo": "Cierre diario de alertas antes del siguiente servicio",
            "accion": "Crear checklist de direccion: APPCC, buffet, caja, RRHH, mantenimiento y quejas con responsable y fecha limite.",
            "indicador": "Alertas altas abiertas al cierre igual a cero",
            "impacto": "Menos reincidencia y menos problemas que se arrastran varios dias.",
        },
        "contexto_externo": {
            "area": "estrategia",
            "titulo": "Cruzar senales externas con decisiones comerciales",
            "accion": "Cuando haya huelga, ola de calor o evento local, recalcular precio, plantilla, compras y mensajes al cliente.",
            "indicador": "Decisiones con fuente externa y datos internos asociados",
            "impacto": "Anticipa cambios de demanda sin improvisar.",
        },
        "appcc": {
            "area": "appcc",
            "titulo": "APPCC bloqueante en produccion y buffet",
            "accion": "No permitir publicar servicio o imprimir tarjetas si hay control critico sin accion correctiva.",
            "indicador": "No conformidades sin corregir igual a cero",
            "impacto": "Reduce riesgo sanitario y reputacional.",
        },
        "mantenimiento": {
            "area": "mantenimiento",
            "titulo": "Averias repetidas pasan a preventivo",
            "accion": "Agrupar averias por activo/zona y abrir plan preventivo si se repiten en temporada.",
            "indicador": "Ordenes correctivas altas por activo a la baja",
            "impacto": "Menos habitaciones bloqueadas, quejas y compensaciones.",
        },
        "quejas_cliente": {
            "area": "experiencia",
            "titulo": "Quejas repetidas se convierten en regla preventiva",
            "accion": "Cruzar quejas con turno, departamento y ocupacion; si se repite, crear accion operativa obligatoria.",
            "indicador": "Quejas por 100 pax a la baja",
            "impacto": "Mejora satisfaccion y fidelizacion.",
        },
        "overbooking": {
            "area": "recepcion",
            "titulo": "Protocolo de overbooking y habitaciones en conflicto",
            "accion": "Crear cola diaria de conflictos: doble venta, reserva sin asignar y bloqueo sobre reserva. Resolver antes de llegada con upgrade, recolocacion o hotel alternativo.",
            "indicador": "Reservas conflictivas pendientes antes de las 12:00 igual a cero",
            "impacto": "Evita llegadas sin solucion, compensaciones caras y dano reputacional directo.",
        },
    }
    created = 0
    for key, data in sorted(counters.items(), key=lambda item: (item[1]["dias"], abs(item[1]["margen"])), reverse=True):
        definition = definitions.get(key, {
            "area": "direccion",
            "titulo": f"Revisar causa {key}",
            "accion": "Auditar datos, formula y eventos asociados antes de tomar decision.",
            "indicador": "Causa explicada o descartada",
            "impacto": "Evita decisiones basadas en ruido.",
        })
        prioridad = "critica" if data["dias"] >= 20 or data["margen"] < -20000 else "alta" if data["dias"] >= 8 else "media"
        conn.execute(
            """
            INSERT INTO simulador_vida_plan_accion
            (corrida_id, area, prioridad, titulo, problema, accion_recomendada,
             indicador_objetivo, impacto_estimado, evidencia_json, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 3)
            """,
            (
                corrida_id,
                definition["area"],
                prioridad,
                definition["titulo"],
                f"Aparece en {data['dias']} diagnosticos. Margen acumulado asociado: {data['margen']:.2f} EUR.",
                definition["accion"],
                definition["indicador"],
                definition["impacto"],
                json.dumps(data, ensure_ascii=False),
            ),
        )
        created += 1
    return created


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


if __name__ == "__main__":
    main()

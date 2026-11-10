# -*- coding: utf-8 -*-
"""Genera un veredicto honesto sobre la simulacion.

La idea no es vender humo: combina salud tecnica, trazabilidad, finanzas,
operativa y RRHH para decir si la ERP aguanta una prueba real o donde rompe.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Veredicto brutal de la simulacion Hotel 1.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=3)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def scalar(conn: sqlite3.Connection, sql: str, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        return row[0] if row[0] is not None else default
    except Exception:
        return default


def one(conn: sqlite3.Connection, sql: str, params=()) -> dict:
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []


def latest_corrida(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "simulador_vida_corridas"):
        return 0
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_veredicto_brutal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            nivel TEXT,
            nota INTEGER,
            titular TEXT,
            veredicto TEXT,
            conclusion TEXT,
            fortalezas_json TEXT,
            roturas_json TEXT,
            acciones_json TEXT,
            metricas_json TEXT,
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def classify(nota: float, roturas_criticas: int, roturas_altas: int) -> tuple[str, str]:
    if roturas_criticas > 0 or nota < 45:
        return "roto", "La ERP rompe en puntos criticos antes de poder confiar en la IA."
    if roturas_altas >= 4 or nota < 65:
        return "verde", "La ERP tiene base, pero todavia esta verde para una prueba real exigente."
    if nota < 82:
        return "camino", "La ERP va por el camino correcto, con roturas concretas que hay que cerrar."
    return "fuerte", "La ERP aguanta bastante bien la prueba; ahora toca endurecer casos reales."


def build_verdict(conn: sqlite3.Connection, corrida_id: int, empresa_id: int, centro_id: int) -> dict:
    calidad = one(
        conn,
        "SELECT * FROM simulador_calidad_resumen WHERE corrida_id=? AND centro_id=? ORDER BY id DESC LIMIT 1",
        (corrida_id, centro_id),
    ) if table_exists(conn, "simulador_calidad_resumen") else {}
    traz = one(
        conn,
        "SELECT * FROM simulador_trazabilidad_resumen WHERE corrida_id=? AND centro_id=? ORDER BY id DESC LIMIT 1",
        (corrida_id, centro_id),
    ) if table_exists(conn, "simulador_trazabilidad_resumen") else {}
    rutas = one(
        conn,
        "SELECT * FROM simulador_prueba_fuego_resumen WHERE corrida_id=? AND centro_id=? ORDER BY id DESC LIMIT 1",
        (corrida_id, centro_id),
    ) if table_exists(conn, "simulador_prueba_fuego_resumen") else {}
    fin = one(
        conn,
        """
        SELECT COUNT(*) meses,
               SUM(CASE WHEN margen < 0 THEN 1 ELSE 0 END) meses_negativos,
               SUM(dias_negativos) dias_negativos,
               ROUND(SUM(ingresos_total), 2) ingresos,
               ROUND(SUM(coste_total), 2) costes,
               ROUND(SUM(margen), 2) margen,
               ROUND(AVG(precio_medio_habitacion), 2) precio_medio
        FROM simulador_resultado_mensual
        WHERE corrida_id=? AND centro_id=?
        """,
        (corrida_id, centro_id),
    ) if table_exists(conn, "simulador_resultado_mensual") else {}
    rrhh = one(
        conn,
        """
        SELECT COUNT(*) dias,
               SUM(CASE WHEN nivel IN ('critica','alta') THEN 1 ELSE 0 END) dias_tension,
               SUM(CASE WHEN nivel='critica' THEN 1 ELSE 0 END) dias_criticos,
               SUM(sin_fichar) sin_fichar,
               SUM(fichaje_sin_turno) fichaje_sin_turno,
               ROUND(SUM(coste_riesgo_estimado), 2) coste_riesgo
        FROM simulador_rrhh_control_diario
        WHERE corrida_id=? AND centro_id=?
        """,
        (corrida_id, centro_id),
    ) if table_exists(conn, "simulador_rrhh_control_diario") else {}
    rrhh_tensionado = one(
        conn,
        """
        SELECT *
        FROM simulador_rrhh_escenarios_resumen
        WHERE corrida_id=? AND centro_id=? AND modo='tensionado'
        ORDER BY id DESC LIMIT 1
        """,
        (corrida_id, centro_id),
    ) if table_exists(conn, "simulador_rrhh_escenarios_resumen") else {}
    rrhh_caos = one(
        conn,
        """
        SELECT *
        FROM simulador_rrhh_escenarios_resumen
        WHERE corrida_id=? AND centro_id=? AND modo='caos'
        ORDER BY id DESC LIMIT 1
        """,
        (corrida_id, centro_id),
    ) if table_exists(conn, "simulador_rrhh_escenarios_resumen") else {}
    prod = one(
        conn,
        """
        SELECT COUNT(*) dias,
               SUM(partes) partes,
               SUM(lineas) lineas,
               SUM(ingredientes_reales) ingredientes,
               SUM(controles_appcc) appcc,
               SUM(incidencias) incidencias
        FROM simulador_produccion_resumen_diario
        WHERE corrida_id=? AND centro_id=?
        """,
        (corrida_id, centro_id),
    ) if table_exists(conn, "simulador_produccion_resumen_diario") else {}
    overbooking = one(
        conn,
        """
        SELECT COUNT(*) casos,
               SUM(CASE WHEN estado_gestion IN ('pendiente','en_gestion') THEN 1 ELSE 0 END) sin_resolver,
               SUM(CASE WHEN estado_gestion='resuelto_simulado' THEN 1 ELSE 0 END) resueltos,
               ROUND(SUM(coste_estimado), 2) coste_estimado
        FROM simulador_overbooking_casos
        WHERE corrida_id=? AND centro_id=?
        """,
        (corrida_id, centro_id),
    ) if table_exists(conn, "simulador_overbooking_casos") else {}
    eventos = rows(
        conn,
        """
        SELECT modulo, severidad, COUNT(*) total
        FROM simulador_vida_eventos
        WHERE corrida_id=? AND centro_id=? AND severidad IN ('critica','alta')
        GROUP BY modulo, severidad
        ORDER BY CASE severidad WHEN 'critica' THEN 1 ELSE 2 END, total DESC
        LIMIT 12
        """,
        (corrida_id, centro_id),
    ) if table_exists(conn, "simulador_vida_eventos") else []

    roturas: list[dict] = []
    fortalezas: list[str] = []
    acciones: list[str] = []

    calidad_score = float(calidad.get("score_confianza") or 0)
    traz_score = float(traz.get("score") or 0)
    rutas_score = float(rutas.get("score") or 0)

    if calidad_score >= 95:
        fortalezas.append("La coherencia base de calendario, KPIs y formulas ya no esta rota.")
    else:
        roturas.append({"nivel": "alta", "area": "calidad", "texto": f"Calidad de simulacion baja: {calidad_score:.1f}."})

    if traz_score >= 90:
        fortalezas.append("La trazabilidad entre modulos principales aguanta: reservas, compras, produccion, TPV y mantenimiento conectan.")
    else:
        roturas.append({"nivel": "alta", "area": "trazabilidad", "texto": f"Trazabilidad insuficiente: {traz_score:.1f}."})

    if rutas_score >= 98:
        fortalezas.append("Las pantallas principales cargan sin romper en la prueba de fuego.")
    else:
        roturas.append({"nivel": "critica", "area": "pantallas", "texto": f"Hay rutas que fallan: score {rutas_score:.1f}."})

    meses = int(fin.get("meses") or 0)
    meses_neg = int(fin.get("meses_negativos") or 0)
    margen = float(fin.get("margen") or 0)
    dias_neg = int(fin.get("dias_negativos") or 0)
    if meses:
        if margen > 0 and meses_neg <= 2:
            fortalezas.append(f"La campana completa da margen positivo simulado: {margen:,.2f} EUR.")
        if meses_neg:
            roturas.append({"nivel": "alta" if meses_neg >= 2 else "media", "area": "finanzas", "texto": f"{meses_neg} meses en negativo y {dias_neg} dias negativos. No basta con decir que el total gana."})
            acciones.append("Crear diagnostico de precio, ocupacion, canal, coste fijo y coste laboral para cada mes negativo.")
    else:
        roturas.append({"nivel": "alta", "area": "finanzas", "texto": "No hay resultado mensual: la IA no puede opinar de rentabilidad."})

    rrhh_eval = rrhh_tensionado or rrhh
    rrhh_dias = int(rrhh_eval.get("dias") or 0)
    rrhh_criticos = int(rrhh_eval.get("dias_criticos") or 0)
    if rrhh_dias and rrhh_criticos / max(rrhh_dias, 1) > 0.70:
        roturas.append({"nivel": "alta", "area": "rrhh", "texto": f"RRHH rompe incluso en modo tensionado: {rrhh_criticos}/{rrhh_dias} dias criticos. Asi no se puede validar nomina ni estabilidad."})
        if not rrhh_tensionado:
            acciones.append("Crear modo de simulacion RRHH normal, tensionado y caos; ahora todo esta demasiado en modo caos.")
        else:
            acciones.append("Ajustar coberturas y fichajes del modo tensionado hasta que solo fallen los dias realmente complicados.")
    elif rrhh_dias:
        fortalezas.append("RRHH ya se puede leer por escenarios: normal, tensionado y caos.")
    else:
        roturas.append({"nivel": "critica", "area": "rrhh", "texto": "No hay diagnostico diario de RRHH."})

    prod_dias = int(prod.get("dias") or 0)
    prod_lineas = int(prod.get("lineas") or 0)
    prod_appcc = int(prod.get("appcc") or 0)
    if prod_dias and prod_lineas and prod_appcc >= prod_lineas * 0.95:
        fortalezas.append("Produccion tiene ingredientes reales, salida de camaras y APPCC casi por linea.")
    else:
        roturas.append({"nivel": "alta", "area": "produccion", "texto": "Produccion aun no acredita APPCC o ingredientes por linea con suficiente cobertura."})

    over_casos = int(overbooking.get("casos") or 0)
    over_sin_resolver = int(overbooking.get("sin_resolver") or 0)
    over_resueltos = int(overbooking.get("resueltos") or 0)
    if over_casos:
        if over_sin_resolver:
            roturas.append({"nivel": "critica", "area": "recepcion", "texto": f"{over_sin_resolver}/{over_casos} overbookings siguen pendientes o en gestion. Detectarlos no basta; hay que cerrarlos."})
            acciones.append("Convertir propuestas de overbooking en resoluciones ejecutadas: reasignacion, desvio cadena, hotel apoyo y bloqueo de cupos.")
        if over_resueltos:
            fortalezas.append(f"Recepcion detecta overbooking y ya tiene {over_resueltos} casos resueltos en simulacion.")

    if not acciones:
        acciones.append("Endurecer la simulacion con casos de temporada, proveedores, sobreventa, quejas y rotacion sin romper trazabilidad.")
    acciones.append("Separar claramente resultado del negocio y calidad tecnica de la ERP: ganar dinero en el simulador no significa que la ERP este terminada.")
    acciones.append("La IA debe explicar cada recomendacion con dato interno y causa externa; sin evidencia, debe decir 'no lo se'.")

    roturas_criticas = sum(1 for r in roturas if r["nivel"] == "critica")
    roturas_altas = sum(1 for r in roturas if r["nivel"] == "alta")
    base_score = (calidad_score * 0.25) + (traz_score * 0.25) + (rutas_score * 0.20)
    fin_score = 100 - min(35, meses_neg * 8 + max(0, dias_neg - 15) * 0.25)
    rrhh_score = 100 - min(45, (rrhh_criticos / max(rrhh_dias, 1)) * 45 if rrhh_dias else 45)
    prod_score = 95 if prod_dias and prod_lineas and prod_appcc >= prod_lineas * 0.95 else 55
    nota = round(base_score + fin_score * 0.12 + rrhh_score * 0.10 + prod_score * 0.08)
    nota = max(0, min(100, nota - roturas_criticas * 12 - max(0, roturas_altas - 2) * 3))
    nivel, conclusion = classify(nota, roturas_criticas, roturas_altas)

    if nivel == "fuerte":
        titular = "Pepe, esto ya no es una mierda: esta cogiendo cuerpo."
    elif nivel == "camino":
        titular = "Pepe, vamos por el camino, pero aun no lo firmaria para una explotacion real."
    elif nivel == "verde":
        titular = "Pepe, la idea es buena, pero la ERP todavia esta verde para jugarse dinero real."
    else:
        titular = "Pepe, aqui rompe demasiado: antes de IA bonita hay que arreglar cimientos."

    veredicto = (
        f"Nota {nota}/100. "
        f"Calidad {calidad_score:.1f}, trazabilidad {traz_score:.1f}, pantallas {rutas_score:.1f}. "
        f"El sistema ya genera y cruza mucha operativa, pero el veredicto depende de cerrar las roturas, no de que la pantalla se vea bien."
    )
    metricas = {
        "calidad": calidad,
        "trazabilidad": traz,
        "rutas": rutas,
        "finanzas": fin,
        "rrhh": rrhh,
        "rrhh_tensionado": rrhh_tensionado,
        "rrhh_caos": rrhh_caos,
        "produccion": prod,
        "overbooking": overbooking,
        "eventos_top": eventos,
    }
    return {
        "corrida_id": corrida_id,
        "nivel": nivel,
        "nota": int(nota),
        "titular": titular,
        "veredicto": veredicto,
        "conclusion": conclusion,
        "fortalezas": fortalezas[:8],
        "roturas": roturas[:12],
        "acciones": acciones[:8],
        "metricas": metricas,
        "empresa_id": empresa_id,
        "centro_id": centro_id,
    }


def persist(conn: sqlite3.Connection, verdict: dict) -> None:
    conn.execute(
        "DELETE FROM simulador_veredicto_brutal WHERE corrida_id=? AND empresa_id=? AND centro_id=?",
        (verdict["corrida_id"], verdict["empresa_id"], verdict["centro_id"]),
    )
    conn.execute(
        """
        INSERT INTO simulador_veredicto_brutal
        (corrida_id, nivel, nota, titular, veredicto, conclusion, fortalezas_json,
         roturas_json, acciones_json, metricas_json, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            verdict["corrida_id"],
            verdict["nivel"],
            verdict["nota"],
            verdict["titular"],
            verdict["veredicto"],
            verdict["conclusion"],
            json.dumps(verdict["fortalezas"], ensure_ascii=False),
            json.dumps(verdict["roturas"], ensure_ascii=False),
            json.dumps(verdict["acciones"], ensure_ascii=False),
            json.dumps(verdict["metricas"], ensure_ascii=False),
            verdict["empresa_id"],
            verdict["centro_id"],
        ),
    )
    conn.commit()


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
            raise SystemExit("No hay corrida para auditar.")
        verdict = build_verdict(conn, corrida_id, args.empresa_id, args.centro_id)
        persist(conn, verdict)
        print({k: verdict[k] for k in ("corrida_id", "nivel", "nota", "titular", "conclusion")})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

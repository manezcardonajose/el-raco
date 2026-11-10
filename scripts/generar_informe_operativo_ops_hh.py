# -*- coding: utf-8 -*-
"""Genera un informe normalizado OPS/H&H desde una simulacion operativa.

Este script no simula. Lee una base de laboratorio o una base real con tablas
de simulador y produce una salida unica para analizar si la operacion de un
establecimiento es adecuada:

- mano de obra;
- cocina, compras y produccion;
- ocupacion e ingresos;
- costes fijos/variables;
- calidad del dato;
- riesgos y plan de accion.

La regla es estricta: si falta una fuente, el informe lo declara como dato
faltante. No rellena con valores inventados.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
DEFAULT_JSON = ROOT / "publicaciones" / "el_raco" / "informe_operativo_ops_hh.json"
DEFAULT_MD = ROOT / "docs" / "informe_operativo_ops_hh.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Informe operativo OPS/H&H desde simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Base origen. Puede ser laboratorio o real.")
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=3)
    parser.add_argument("--corrida-id", type=int, default=0, help="0 = ultima corrida disponible.")
    parser.add_argument("--out-json", default=str(DEFAULT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_MD))
    parser.add_argument("--sin-guardar-db", action="store_true", help="No guarda resumen en tabla simulador_ops_hh_informes.")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def scalar(conn: sqlite3.Connection, sql: str, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        value = row[0]
        return default if value is None else value
    except sqlite3.Error:
        return default


def rows(conn: sqlite3.Connection, sql: str, params=()) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def money(value) -> float:
    try:
        return round(float(value or 0), 2)
    except Exception:
        return 0.0


def pct(value) -> float:
    try:
        value = float(value or 0)
    except Exception:
        value = 0.0
    if abs(value) <= 1:
        value *= 100
    return round(value, 2)


def fmt_money(value) -> str:
    return f"{money(value):,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_pct(value) -> str:
    return f"{pct(value):.2f}%".replace(".", ",")


def latest_corrida(conn: sqlite3.Connection) -> int:
    for table in ("simulador_vida_corridas", "simulador_corridas_generales"):
        if table_exists(conn, table):
            row = conn.execute(f"SELECT id FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                return int(row["id"])
    return 0


def centro_nombre(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> str:
    for table in ("centros_trabajo", "centros"):
        if table_exists(conn, table):
            cols = columns(conn, table)
            empresa_clause = "AND empresa_id=?" if "empresa_id" in cols else ""
            params = [centro_id]
            if empresa_clause:
                params.append(empresa_id)
            row = conn.execute(
                f"SELECT nombre FROM {table} WHERE id=? {empresa_clause} LIMIT 1",
                params,
            ).fetchone()
            if row and row["nombre"]:
                return str(row["nombre"])
    return f"Centro {centro_id}"


def capacidad(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> dict:
    habitaciones = 0
    plazas = 0
    for table in ("habitaciones_recepcion", "habitaciones"):
        if not table_exists(conn, table):
            continue
        cols = columns(conn, table)
        filtros = []
        params = []
        if "empresa_id" in cols:
            filtros.append("empresa_id=?")
            params.append(empresa_id)
        if "centro_id" in cols:
            filtros.append("centro_id=?")
            params.append(centro_id)
        if "activa" in cols:
            filtros.append("COALESCE(activa,1)=1")
        elif "activo" in cols:
            filtros.append("COALESCE(activo,1)=1")
        where = "WHERE " + " AND ".join(filtros) if filtros else ""
        habitaciones = int(scalar(conn, f"SELECT COUNT(*) FROM {table} {where}", params, 0))
        plaza_cols = [c for c in ("plazas", "capacidad", "capacidad_maxima") if c in cols]
        if plaza_cols:
            plazas = int(scalar(conn, f"SELECT SUM(COALESCE({plaza_cols[0]},0)) FROM {table} {where}", params, 0))
        break
    return {"habitaciones": habitaciones, "plazas": plazas}


def monthly_finance(conn: sqlite3.Connection, corrida_id: int, empresa_id: int, centro_id: int) -> dict:
    if not table_exists(conn, "simulador_resultado_mensual"):
        return {"disponible": False, "motivo": "Falta simulador_resultado_mensual", "meses": []}
    filtros = ["centro_id=?"]
    params: list = [centro_id]
    cols = columns(conn, "simulador_resultado_mensual")
    if "empresa_id" in cols:
        filtros.append("empresa_id=?")
        params.append(empresa_id)
    if corrida_id and "corrida_id" in cols:
        filtros.append("corrida_id=?")
        params.append(corrida_id)
    data = [dict(r) for r in rows(
        conn,
        f"""
        SELECT *
        FROM simulador_resultado_mensual
        WHERE {' AND '.join(filtros)}
        ORDER BY mes
        """,
        params,
    )]
    ingresos = sum(money(r.get("ingresos_total")) for r in data)
    costes = sum(money(r.get("coste_total")) for r in data)
    laboral = sum(money(r.get("coste_laboral")) for r in data)
    compras = sum(money(r.get("coste_compras")) for r in data)
    fijo = sum(money(r.get("coste_fijo")) for r in data)
    margen = sum(money(r.get("margen")) for r in data)
    dias_negativos = sum(int(r.get("dias_negativos") or 0) for r in data)
    return {
        "disponible": bool(data),
        "meses": data,
        "totales": {
            "ingresos": money(ingresos),
            "coste_total": money(costes),
            "coste_laboral": money(laboral),
            "coste_compras": money(compras),
            "coste_fijo": money(fijo),
            "margen": money(margen),
            "margen_pct": pct(margen / ingresos if ingresos else 0),
            "coste_laboral_pct": pct(laboral / ingresos if ingresos else 0),
            "coste_compras_pct": pct(compras / ingresos if ingresos else 0),
            "coste_fijo_pct": pct(fijo / ingresos if ingresos else 0),
            "dias_negativos": dias_negativos,
        },
    }


def daily_kpis(conn: sqlite3.Connection, corrida_id: int, empresa_id: int, centro_id: int) -> dict:
    if not table_exists(conn, "simulador_vida_kpis_diarios"):
        return {"disponible": False, "motivo": "Falta simulador_vida_kpis_diarios"}
    filtros = ["centro_id=?"]
    params: list = [centro_id]
    cols = columns(conn, "simulador_vida_kpis_diarios")
    if "empresa_id" in cols:
        filtros.append("empresa_id=?")
        params.append(empresa_id)
    if corrida_id and "corrida_id" in cols:
        filtros.append("corrida_id=?")
        params.append(corrida_id)
    data = [dict(r) for r in rows(
        conn,
        f"SELECT * FROM simulador_vida_kpis_diarios WHERE {' AND '.join(filtros)} ORDER BY fecha",
        params,
    )]
    if not data:
        return {"disponible": False, "motivo": "No hay KPIs diarios para ese centro/corrida"}
    ingresos_hab = sum(money(r.get("ingresos_habitaciones")) for r in data)
    ingresos_tpv = sum(money(r.get("ingresos_tpv")) for r in data)
    margen = sum(money(r.get("margen_estimado")) for r in data)
    habitaciones = sum(int(r.get("habitaciones_ocupadas") or 0) for r in data)
    pax = sum(int(r.get("pax") or 0) for r in data)
    riesgos = [money(r.get("riesgo_operativo")) for r in data]
    return {
        "disponible": True,
        "dias": len(data),
        "fecha_desde": data[0].get("fecha"),
        "fecha_hasta": data[-1].get("fecha"),
        "ingresos_habitaciones": money(ingresos_hab),
        "ingresos_tpv": money(ingresos_tpv),
        "margen_estimado": money(margen),
        "habitaciones_ocupadas": habitaciones,
        "pax": pax,
        "adr": money(ingresos_hab / habitaciones if habitaciones else 0),
        "tpv_pax": money(ingresos_tpv / pax if pax else 0),
        "riesgo_medio": round(mean(riesgos), 2) if riesgos else 0,
        "quejas": sum(int(r.get("quejas") or 0) for r in data),
        "appcc_no_conformidades": sum(int(r.get("no_conformidades_appcc") or 0) for r in data),
        "ordenes_mantenimiento": sum(int(r.get("ordenes_mantenimiento") or 0) for r in data),
        "alertas_altas": sum(int(r.get("alertas_altas") or 0) for r in data),
    }


def labor(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> dict:
    result = {
        "disponible": table_exists(conn, "empleados"),
        "empleados_activos": 0,
        "departamentos": [],
        "coste_prenomina": 0.0,
        "horas_normales": 0.0,
        "horas_extra_aprobadas": 0.0,
        "ausencias_horas": 0.0,
        "alertas": [],
    }
    if not result["disponible"]:
        result["alertas"].append("Falta tabla empleados.")
        return result
    cols = columns(conn, "empleados")
    filtros = []
    params = []
    if "empresa_id" in cols:
        filtros.append("e.empresa_id=?")
        params.append(empresa_id)
    if "centro_id" in cols:
        filtros.append("e.centro_id=?")
        params.append(centro_id)
    filtros.append("COALESCE(e.activo,1)=1")
    where = " AND ".join(filtros)
    dept_join = "LEFT JOIN departamentos d ON d.id=e.departamento_id" if table_exists(conn, "departamentos") else ""
    dept_select = "COALESCE(d.nombre,'Sin departamento')" if dept_join else "'Sin departamento'"
    dept_rows = rows(
        conn,
        f"""
        SELECT {dept_select} AS departamento, COUNT(*) AS empleados
        FROM empleados e
        {dept_join}
        WHERE {where}
        GROUP BY {dept_select}
        ORDER BY empleados DESC
        """,
        params,
    )
    result["empleados_activos"] = sum(int(r["empleados"] or 0) for r in dept_rows)
    result["departamentos"] = [{"departamento": r["departamento"], "empleados": int(r["empleados"] or 0)} for r in dept_rows]
    if table_exists(conn, "resumen_prenomina"):
        join = "JOIN empleados e ON e.id=rp.empleado_id AND e.empresa_id=rp.empresa_id"
        pre = rows(
            conn,
            f"""
            SELECT
              SUM(COALESCE(rp.coste_estimado,0)) AS coste,
              SUM(COALESCE(rp.horas_normales,0)) AS horas,
              SUM(COALESCE(rp.horas_extra_aprobadas,0)) AS extras,
              SUM(COALESCE(rp.ausencias_horas,0)) AS ausencias
            FROM resumen_prenomina rp
            {join}
            WHERE {where}
            """,
            params,
        )
        if pre:
            row = pre[0]
            result["coste_prenomina"] = money(row["coste"])
            result["horas_normales"] = money(row["horas"])
            result["horas_extra_aprobadas"] = money(row["extras"])
            result["ausencias_horas"] = money(row["ausencias"])
    else:
        result["alertas"].append("Falta resumen_prenomina; no se puede validar coste laboral.")
    return result


def kitchen(conn: sqlite3.Connection, corrida_id: int, empresa_id: int, centro_id: int) -> dict:
    result = {
        "disponible": False,
        "partes": 0,
        "recetas": 0,
        "raciones": 0.0,
        "coste_teorico": 0.0,
        "coste_real": 0.0,
        "desviacion": 0.0,
        "merma": 0.0,
        "reaprovechado": 0.0,
        "riesgos_altos": 0,
        "platos_top_coste": [],
        "impacto_mensual": [],
        "alertas": [],
    }
    if table_exists(conn, "simulador_escandallos_diarios_plato"):
        result["disponible"] = True
        filtros = ["centro_id=?"]
        params: list = [centro_id]
        cols = columns(conn, "simulador_escandallos_diarios_plato")
        if "empresa_id" in cols:
            filtros.append("empresa_id=?")
            params.append(empresa_id)
        if corrida_id and "corrida_id" in cols:
            filtros.append("corrida_id=?")
            params.append(corrida_id)
        row = rows(
            conn,
            f"""
            SELECT
              COUNT(DISTINCT parte_id) AS partes,
              COUNT(DISTINCT receta_id) AS recetas,
              SUM(COALESCE(raciones_reales,0)) AS raciones,
              SUM(COALESCE(coste_teorico,0)) AS teorico,
              SUM(COALESCE(coste_real,0)) AS real,
              SUM(COALESCE(desviacion_coste,0)) AS desviacion,
              SUM(COALESCE(coste_merma_estimado,0)) AS merma,
              SUM(COALESCE(coste_reaprovechado_estimado,0)) AS reaprovechado,
              SUM(CASE WHEN COALESCE(riesgo,'') IN ('alto','critico','critica') THEN 1 ELSE 0 END) AS riesgos_altos
            FROM simulador_escandallos_diarios_plato
            WHERE {' AND '.join(filtros)}
            """,
            params,
        )[0]
        result.update({
            "partes": int(row["partes"] or 0),
            "recetas": int(row["recetas"] or 0),
            "raciones": money(row["raciones"]),
            "coste_teorico": money(row["teorico"]),
            "coste_real": money(row["real"]),
            "desviacion": money(row["desviacion"]),
            "merma": money(row["merma"]),
            "reaprovechado": money(row["reaprovechado"]),
            "riesgos_altos": int(row["riesgos_altos"] or 0),
        })
        top = rows(
            conn,
            f"""
            SELECT plato, SUM(COALESCE(coste_real,0)) AS coste, SUM(COALESCE(coste_merma_estimado,0)) AS merma
            FROM simulador_escandallos_diarios_plato
            WHERE {' AND '.join(filtros)}
            GROUP BY plato
            ORDER BY coste DESC
            LIMIT 8
            """,
            params,
        )
        result["platos_top_coste"] = [dict(r) for r in top]
    else:
        result["alertas"].append("Falta simulador_escandallos_diarios_plato; cocina no es auditable por plato.")
    if table_exists(conn, "simulador_impacto_cocina_mensual"):
        impacto = rows(
            conn,
            """
            SELECT mes, platos_intervenidos, coste_afectado, merma_afectada, ahorro_estimado,
                   margen_antes, margen_despues, estado_antes, estado_despues, decision
            FROM simulador_impacto_cocina_mensual
            WHERE centro_id=? AND empresa_id=? AND (?=0 OR corrida_id=?)
            ORDER BY mes
            """,
            (centro_id, empresa_id, corrida_id, corrida_id),
        )
        result["impacto_mensual"] = [dict(r) for r in impacto]
    return result


def purchases(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> dict:
    result = {"disponible": False, "pedidos": 0, "recepciones": 0, "validadas_operativas": 0, "validadas_economicas": 0, "alertas": []}
    if table_exists(conn, "pedidos"):
        result["disponible"] = True
        cols = columns(conn, "pedidos")
        filtros = []
        params = []
        if "empresa_id" in cols:
            filtros.append("empresa_id=?")
            params.append(empresa_id)
        # Pedidos no siempre tienen centro_id; se respeta lo disponible sin inventar.
        if "centro_id" in cols:
            filtros.append("centro_id=?")
            params.append(centro_id)
        where = "WHERE " + " AND ".join(filtros) if filtros else ""
        result["pedidos"] = int(scalar(conn, f"SELECT COUNT(*) FROM pedidos {where}", params, 0))
        if "centro_id" not in cols:
            result["alertas"].append("Pedidos sin centro_id; solo se puede filtrar por empresa.")
    else:
        result["alertas"].append("Falta tabla pedidos.")
    if table_exists(conn, "recepciones"):
        cols = columns(conn, "recepciones")
        filtros = []
        params = []
        if "empresa_id" in cols:
            filtros.append("empresa_id=?")
            params.append(empresa_id)
        if "centro_id" in cols:
            filtros.append("centro_id=?")
            params.append(centro_id)
        where = "WHERE " + " AND ".join(filtros) if filtros else ""
        row = rows(
            conn,
            f"""
            SELECT COUNT(*) total,
                   SUM(COALESCE(validada_operativamente,0)) op,
                   SUM(COALESCE(validada_economicamente,0)) eco
            FROM recepciones {where}
            """,
            params,
        )[0]
        result["recepciones"] = int(row["total"] or 0)
        result["validadas_operativas"] = int(row["op"] or 0)
        result["validadas_economicas"] = int(row["eco"] or 0)
        if "centro_id" not in cols:
            result["alertas"].append("Recepciones sin centro_id; solo se puede filtrar por empresa.")
    return result


def reservations(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> dict:
    if not table_exists(conn, "reservas_recepcion"):
        return {"disponible": False, "motivo": "Falta reservas_recepcion"}
    cols = columns(conn, "reservas_recepcion")
    filtros = []
    params = []
    if "empresa_id" in cols:
        filtros.append("empresa_id=?")
        params.append(empresa_id)
    if "centro_id" in cols:
        filtros.append("centro_id=?")
        params.append(centro_id)
    where = "WHERE " + " AND ".join(filtros) if filtros else ""
    row = rows(
        conn,
        f"""
        SELECT COUNT(*) reservas,
               SUM(COALESCE(adultos,0)+COALESCE(menores,0)) pax,
               SUM(COALESCE(precio_total,0)) ingresos,
               SUM(COALESCE(comision_canal,0)) comisiones
        FROM reservas_recepcion {where}
        """,
        params,
    )[0]
    by_status = rows(
        conn,
        f"""
        SELECT COALESCE(estado,'sin_estado') estado, COUNT(*) total
        FROM reservas_recepcion {where}
        GROUP BY COALESCE(estado,'sin_estado')
        ORDER BY total DESC
        """,
        params,
    )
    return {
        "disponible": True,
        "reservas": int(row["reservas"] or 0),
        "pax": int(row["pax"] or 0),
        "ingresos": money(row["ingresos"]),
        "comisiones": money(row["comisiones"]),
        "estados": [dict(r) for r in by_status],
    }


def quality(conn: sqlite3.Connection, corrida_id: int, empresa_id: int, centro_id: int) -> dict:
    out = {"score_confianza": 0.0, "lectura": "Sin auditoria de calidad.", "incidencias": []}
    if table_exists(conn, "simulador_calidad_resumen"):
        row = conn.execute(
            """
            SELECT *
            FROM simulador_calidad_resumen
            WHERE empresa_id=? AND centro_id=? AND (?=0 OR corrida_id=?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (empresa_id, centro_id, corrida_id, corrida_id),
        ).fetchone()
        if row:
            out.update({
                "score_confianza": money(row["score_confianza"]),
                "lectura": row["lectura"],
                "total": int(row["total"] or 0),
                "criticas": int(row["criticas"] or 0),
                "altas": int(row["altas"] or 0),
                "medias": int(row["medias"] or 0),
                "bajas": int(row["bajas"] or 0),
            })
    if table_exists(conn, "simulador_calidad_datos"):
        out["incidencias"] = [
            dict(r)
            for r in rows(
                conn,
                """
                SELECT area, severidad, codigo, titulo, detalle
                FROM simulador_calidad_datos
                WHERE empresa_id=? AND centro_id=? AND (?=0 OR corrida_id=?)
                  AND COALESCE(estado,'abierta')='abierta'
                ORDER BY CASE severidad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END, id DESC
                LIMIT 20
                """,
                (empresa_id, centro_id, corrida_id, corrida_id),
            )
        ]
    return out


def actions(conn: sqlite3.Connection, corrida_id: int, empresa_id: int, centro_id: int) -> list[dict]:
    if not table_exists(conn, "simulador_vida_plan_accion"):
        return []
    return [
        dict(r)
        for r in rows(
            conn,
            """
            SELECT area, prioridad, titulo, problema, accion_recomendada, indicador_objetivo, impacto_estimado
            FROM simulador_vida_plan_accion
            WHERE empresa_id=? AND centro_id=? AND (?=0 OR corrida_id=?)
              AND COALESCE(estado,'pendiente')='pendiente'
            ORDER BY CASE prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END, id DESC
            LIMIT 12
            """,
            (empresa_id, centro_id, corrida_id, corrida_id),
        )
    ]


def verdict(finance: dict, kpis: dict, labor_data: dict, kitchen_data: dict, quality_data: dict) -> dict:
    missing = []
    for name, block in [
        ("finanzas_mensuales", finance),
        ("kpis_diarios", kpis),
        ("mano_obra", labor_data),
        ("cocina", kitchen_data),
    ]:
        if not block.get("disponible"):
            missing.append(name)
    score = money(quality_data.get("score_confianza"))
    margen = money((finance.get("totales") or {}).get("margen"))
    ingresos = money((finance.get("totales") or {}).get("ingresos"))
    laboral_pct = money((finance.get("totales") or {}).get("coste_laboral_pct"))
    compras_pct = money((finance.get("totales") or {}).get("coste_compras_pct"))
    validable = not missing and score >= 75 and ingresos > 0
    if not validable:
        estado = "no_validable"
        lectura = "La simulacion no debe usarse para decision OPS/H&H hasta completar fuentes faltantes o calidad."
    elif margen < 0:
        estado = "operacion_no_adecuada"
        lectura = "La operacion simulada pierde dinero; OPS/H&H debe proponer cambios antes de asumirla."
    elif laboral_pct > 45:
        estado = "adecuada_con_riesgo_laboral"
        lectura = "La operacion es rentable, pero el peso laboral exige revisar plantilla, turnos y productividad."
    elif compras_pct > 28:
        estado = "adecuada_con_riesgo_cocina"
        lectura = "La operacion es rentable, pero cocina/compras debe revisar escandallos, mermas y proveedores."
    else:
        estado = "adecuada_para_analisis"
        lectura = "La simulacion tiene datos suficientes para que OPS/H&H analice la gestion completa."
    return {
        "estado": estado,
        "validable_ops_hh": validable,
        "lectura": lectura,
        "fuentes_faltantes": missing,
        "regla": "No decidir con fuentes faltantes; no mezclar simulacion con datos reales; contrastar antes de ejecutar.",
    }


def ensure_report_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_ops_hh_informes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            centro_id INTEGER NOT NULL,
            corrida_id INTEGER DEFAULT 0,
            estado TEXT,
            validable_ops_hh INTEGER DEFAULT 0,
            score_confianza REAL DEFAULT 0,
            ingresos REAL DEFAULT 0,
            coste_total REAL DEFAULT 0,
            margen REAL DEFAULT 0,
            coste_laboral_pct REAL DEFAULT 0,
            coste_compras_pct REAL DEFAULT 0,
            lectura TEXT,
            informe_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def save_report(conn: sqlite3.Connection, report: dict) -> None:
    ensure_report_schema(conn)
    fin = report["finanzas"].get("totales") or {}
    ver = report["veredicto"]
    conn.execute(
        """
        INSERT INTO simulador_ops_hh_informes
        (empresa_id, centro_id, corrida_id, estado, validable_ops_hh, score_confianza,
         ingresos, coste_total, margen, coste_laboral_pct, coste_compras_pct, lectura, informe_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report["empresa_id"],
            report["centro_id"],
            report["corrida_id"],
            ver["estado"],
            1 if ver["validable_ops_hh"] else 0,
            money(report["calidad"].get("score_confianza")),
            money(fin.get("ingresos")),
            money(fin.get("coste_total")),
            money(fin.get("margen")),
            money(fin.get("coste_laboral_pct")),
            money(fin.get("coste_compras_pct")),
            ver["lectura"],
            json.dumps(report, ensure_ascii=False, indent=2),
        ),
    )


def render_md(report: dict) -> str:
    fin = report["finanzas"].get("totales") or {}
    lines = [
        "# Informe operativo OPS/H&H",
        "",
        f"- Generado: {report['generado_en']}",
        f"- Empresa: {report['empresa_id']}",
        f"- Centro: {report['centro_id']} - {report['centro_nombre']}",
        f"- Corrida: {report['corrida_id']}",
        f"- Tipo dato: simulacion separada / laboratorio",
        "",
        "## Veredicto",
        "",
        f"- Estado: **{report['veredicto']['estado']}**",
        f"- Validable OPS/H&H: **{'si' if report['veredicto']['validable_ops_hh'] else 'no'}**",
        f"- Lectura: {report['veredicto']['lectura']}",
        f"- Regla: {report['veredicto']['regla']}",
    ]
    if report["veredicto"]["fuentes_faltantes"]:
        lines.append(f"- Fuentes faltantes: {', '.join(report['veredicto']['fuentes_faltantes'])}")
    lines += [
        "",
        "## Capacidad",
        "",
        f"- Habitaciones: {report['capacidad']['habitaciones']}",
        f"- Plazas: {report['capacidad']['plazas']}",
        "",
        "## Finanzas Simuladas",
        "",
        f"- Ingresos: {fmt_money(fin.get('ingresos'))}",
        f"- Coste total: {fmt_money(fin.get('coste_total'))}",
        f"- Coste laboral: {fmt_money(fin.get('coste_laboral'))} ({fmt_pct(fin.get('coste_laboral_pct'))})",
        f"- Coste compras/cocina: {fmt_money(fin.get('coste_compras'))} ({fmt_pct(fin.get('coste_compras_pct'))})",
        f"- Coste fijo: {fmt_money(fin.get('coste_fijo'))} ({fmt_pct(fin.get('coste_fijo_pct'))})",
        f"- Margen: {fmt_money(fin.get('margen'))} ({fmt_pct(fin.get('margen_pct'))})",
        f"- Dias negativos: {fin.get('dias_negativos', 0)}",
        "",
        "## Mano De Obra",
        "",
        f"- Empleados activos: {report['mano_obra']['empleados_activos']}",
        f"- Coste pre-nomina: {fmt_money(report['mano_obra']['coste_prenomina'])}",
        f"- Horas normales: {report['mano_obra']['horas_normales']}",
        f"- Horas extra aprobadas: {report['mano_obra']['horas_extra_aprobadas']}",
        f"- Ausencias horas: {report['mano_obra']['ausencias_horas']}",
    ]
    if report["mano_obra"]["departamentos"]:
        lines += ["", "### Plantilla Por Departamento", ""]
        for d in report["mano_obra"]["departamentos"][:12]:
            lines.append(f"- {d['departamento']}: {d['empleados']} empleados")
    lines += [
        "",
        "## Cocina, Produccion Y Compras",
        "",
        f"- Partes produccion: {report['cocina']['partes']}",
        f"- Recetas/platos: {report['cocina']['recetas']}",
        f"- Raciones reales: {report['cocina']['raciones']}",
        f"- Coste teorico: {fmt_money(report['cocina']['coste_teorico'])}",
        f"- Coste real: {fmt_money(report['cocina']['coste_real'])}",
        f"- Desviacion coste: {fmt_money(report['cocina']['desviacion'])}",
        f"- Merma estimada: {fmt_money(report['cocina']['merma'])}",
        f"- Reaprovechado estimado: {fmt_money(report['cocina']['reaprovechado'])}",
        f"- Lineas con riesgo alto/critico: {report['cocina']['riesgos_altos']}",
        f"- Pedidos: {report['compras']['pedidos']}",
        f"- Recepciones: {report['compras']['recepciones']}",
        f"- Recepciones validadas operativamente: {report['compras']['validadas_operativas']}",
        f"- Recepciones validadas economicamente: {report['compras']['validadas_economicas']}",
    ]
    if report["cocina"]["platos_top_coste"]:
        lines += ["", "### Platos Con Mayor Coste", ""]
        for p in report["cocina"]["platos_top_coste"]:
            lines.append(f"- {p['plato']}: {fmt_money(p['coste'])} / merma {fmt_money(p['merma'])}")
    k = report["kpis_diarios"]
    lines += [
        "",
        "## Operacion Diaria",
        "",
        f"- Dias: {k.get('dias', 0)}",
        f"- Periodo: {k.get('fecha_desde', '-')} a {k.get('fecha_hasta', '-')}",
        f"- ADR: {fmt_money(k.get('adr'))}",
        f"- TPV por pax: {fmt_money(k.get('tpv_pax'))}",
        f"- Riesgo medio: {k.get('riesgo_medio', 0)}",
        f"- Quejas: {k.get('quejas', 0)}",
        f"- APPCC no conformidades: {k.get('appcc_no_conformidades', 0)}",
        f"- Ordenes mantenimiento: {k.get('ordenes_mantenimiento', 0)}",
        f"- Alertas altas: {k.get('alertas_altas', 0)}",
        "",
        "## Calidad Del Dato",
        "",
        f"- Score confianza: {report['calidad'].get('score_confianza', 0)}",
        f"- Lectura: {report['calidad'].get('lectura', '-')}",
    ]
    if report["acciones"]:
        lines += ["", "## Acciones Prioritarias", ""]
        for a in report["acciones"]:
            lines.append(f"- [{a['prioridad']}] {a['titulo']}: {a['accion_recomendada']}")
    alerts = []
    for block in ("mano_obra", "cocina", "compras"):
        alerts.extend(report[block].get("alertas", []))
    if alerts:
        lines += ["", "## Alertas De Lectura", ""]
        for alert in alerts:
            lines.append(f"- {alert}")
    lines.append("")
    return "\n".join(lines)


def build_report(conn: sqlite3.Connection, empresa_id: int, centro_id: int, corrida_id: int) -> dict:
    corrida_id = corrida_id or latest_corrida(conn)
    fin = monthly_finance(conn, corrida_id, empresa_id, centro_id)
    kpis = daily_kpis(conn, corrida_id, empresa_id, centro_id)
    labor_data = labor(conn, empresa_id, centro_id)
    kitchen_data = kitchen(conn, corrida_id, empresa_id, centro_id)
    purchases_data = purchases(conn, empresa_id, centro_id)
    reservations_data = reservations(conn, empresa_id, centro_id)
    quality_data = quality(conn, corrida_id, empresa_id, centro_id)
    actions_data = actions(conn, corrida_id, empresa_id, centro_id)
    report = {
        "generado_en": datetime.now().isoformat(timespec="seconds"),
        "empresa_id": empresa_id,
        "centro_id": centro_id,
        "centro_nombre": centro_nombre(conn, empresa_id, centro_id),
        "corrida_id": corrida_id,
        "tipo_dato": "simulacion_laboratorio",
        "uso_previsto": "OPS/H&H analisis de operacion completa",
        "capacidad": capacidad(conn, empresa_id, centro_id),
        "finanzas": fin,
        "kpis_diarios": kpis,
        "mano_obra": labor_data,
        "cocina": kitchen_data,
        "compras": purchases_data,
        "reservas": reservations_data,
        "calidad": quality_data,
        "acciones": actions_data,
    }
    fin_totales = report["finanzas"].get("totales") or {}
    coste_laboral_financiero = money(fin_totales.get("coste_laboral"))
    coste_prenomina = money(report["mano_obra"].get("coste_prenomina"))
    if coste_laboral_financiero > 0 and coste_prenomina > 0:
        cobertura = coste_prenomina / coste_laboral_financiero
        report["mano_obra"]["cobertura_prenomina_vs_simulador_pct"] = pct(cobertura)
        if cobertura < 0.75:
            report["mano_obra"].setdefault("alertas", []).append(
                "La pre-nomina cubre menos del 75% del coste laboral financiero simulado; usar coste_laboral de simulador mensual como fuente principal hasta cerrar RRHH."
            )
    elif coste_laboral_financiero > 0 and coste_prenomina <= 0:
        report["mano_obra"]["cobertura_prenomina_vs_simulador_pct"] = 0.0
        report["mano_obra"].setdefault("alertas", []).append(
            "Hay coste laboral financiero simulado, pero no hay pre-nomina calculada para contrastarlo."
        )
    report["veredicto"] = verdict(fin, kpis, labor_data, kitchen_data, quality_data)
    return report


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    if not db_path.exists():
        raise SystemExit(f"No existe la base: {db_path}")

    conn = connect(db_path)
    try:
        report = build_report(conn, args.empresa_id, args.centro_id, args.corrida_id)
        if not args.sin_guardar_db:
            save_report(conn, report)
            conn.commit()
    finally:
        conn.close()

    out_json = Path(args.out_json)
    if not out_json.is_absolute():
        out_json = ROOT / out_json
    out_md = Path(args.out_md)
    if not out_md.is_absolute():
        out_md = ROOT / out_md
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(render_md(report), encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "db": str(db_path),
        "json": str(out_json),
        "md": str(out_md),
        "estado": report["veredicto"]["estado"],
        "validable_ops_hh": report["veredicto"]["validable_ops_hh"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

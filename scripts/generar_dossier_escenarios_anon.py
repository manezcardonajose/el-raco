# -*- coding: utf-8 -*-
"""Genera un dossier anonimo de escenarios operativos.

Extrae datos locales de El Raco/Hospitality & Habitat sin exponer nombres reales
de hoteles, empresas, clientes ni empleados.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "dossier_escenarios_operativos_anonimizados.md"
DBS = [
    ("ERP principal", ROOT / "pepe4.db"),
    ("Simulador vida operativa", ROOT / "simulacion_hotel1_vida.db"),
    ("Demo El Raco", ROOT / "simulacion_el_raco.db"),
]

SOURCE_ALIAS = {
    "ERP principal": "Dataset A - operativo",
    "Simulador vida operativa": "Dataset B - simulacion operativa",
    "Demo El Raco": "Dataset C - demo",
}


def money(value) -> str:
    try:
        value = float(value or 0)
    except Exception:
        value = 0.0
    return f"{value:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(value) -> str:
    try:
        value = float(value or 0)
    except Exception:
        value = 0.0
    return f"{value:.1f}%".replace(".", ",")


def clean_text(value) -> str:
    text = str(value or "")
    replacements = {
        "Ã³": "o",
        "Ã¡": "a",
        "Ã©": "e",
        "Ã­": "i",
        "Ãº": "u",
        "Ã±": "n",
        "Âº": "o",
        "Âª": "a",
        "Â": "",
        "ï¸": "",
        "âœ…": "",
        "â€œ": "\"",
        "â€": "\"",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return " ".join(text.split())


def scalar(conn, sql, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        return row[0] if row[0] is not None else default
    except sqlite3.Error:
        return default


def rows(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def table_exists(conn, name: str) -> bool:
    return bool(scalar(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,), None))


def columns(conn, name: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({name})")}
    except sqlite3.Error:
        return set()


def aliases_for_centers(all_centers):
    aliases = {}
    for idx, key in enumerate(sorted(all_centers), start=1):
        aliases[key] = f"Escenario {idx:02d}"
    return aliases


def discover_centers(conn, fuente):
    centros = []
    if table_exists(conn, "centros_trabajo"):
        for r in rows(conn, "SELECT id, empresa_id, activo FROM centros_trabajo ORDER BY empresa_id, id"):
            centros.append(
                {
                    "fuente": fuente,
                    "empresa_id": r["empresa_id"],
                    "centro_id": r["id"],
                    "activo": r["activo"],
                }
            )
    if not centros and table_exists(conn, "empresas"):
        for r in rows(conn, "SELECT id FROM empresas ORDER BY id"):
            centros.append({"fuente": fuente, "empresa_id": r["id"], "centro_id": None, "activo": 1})
    return centros


def center_capacity(conn, empresa_id, centro_id):
    where = ["empresa_id = ?", "COALESCE(activo, 1) = 1"]
    params = [empresa_id]
    if centro_id is not None:
        where.append("(centro_id = ? OR centro_id IS NULL)")
        params.append(centro_id)
    sql_where = " AND ".join(where)
    habitaciones = scalar(conn, f"SELECT COUNT(*) FROM habitaciones_recepcion WHERE {sql_where}", params)
    plazas = scalar(conn, f"SELECT COALESCE(SUM(COALESCE(capacidad, 0)), 0) FROM habitaciones_recepcion WHERE {sql_where}", params)
    tipos = rows(
        conn,
        f"""
        SELECT COALESCE(tipo, 'sin tipo') AS tipo, COUNT(*) AS habitaciones, COALESCE(SUM(COALESCE(capacidad,0)),0) AS plazas
        FROM habitaciones_recepcion
        WHERE {sql_where}
        GROUP BY COALESCE(tipo, 'sin tipo')
        ORDER BY habitaciones DESC
        """,
        params,
    )
    return habitaciones, plazas, tipos


def departments(conn, empresa_id, centro_id):
    if not table_exists(conn, "departamentos"):
        return []
    params = [empresa_id]
    filtro = "empresa_id = ? AND COALESCE(activo, 1) = 1"
    if centro_id is not None and "centro_id" in columns(conn, "departamentos"):
        filtro += " AND (centro_id = ? OR centro_id IS NULL)"
        params.append(centro_id)
    return rows(
        conn,
        f"""
        SELECT nombre, COUNT(*) AS total
        FROM departamentos
        WHERE {filtro}
        GROUP BY nombre
        ORDER BY nombre
        """,
        params,
    )


def categories(conn, empresa_id):
    if not table_exists(conn, "categorias_laborales"):
        return []
    return rows(
        conn,
        """
        SELECT nombre, COALESCE(salario_base, 0) AS salario_base
        FROM categorias_laborales
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY nombre
        """,
        (empresa_id,),
    )


def plantilla(conn, empresa_id, centro_id):
    if not table_exists(conn, "empleados"):
        return {"total": 0, "por_departamento": [], "por_categoria": []}
    cols_emp = columns(conn, "empleados")
    join_dept = "LEFT JOIN departamentos d ON d.id = e.departamento_id" if table_exists(conn, "departamentos") else ""
    join_cat = "LEFT JOIN categorias_laborales c ON c.id = e.categoria_id" if table_exists(conn, "categorias_laborales") else ""
    params = [empresa_id]
    filtro = "e.empresa_id = ? AND COALESCE(e.activo, 1) = 1"
    if centro_id is not None and table_exists(conn, "departamentos") and "centro_id" in columns(conn, "departamentos"):
        filtro += " AND (d.centro_id = ? OR d.centro_id IS NULL)"
        params.append(centro_id)
    total = scalar(conn, f"SELECT COUNT(*) FROM empleados e {join_dept} WHERE {filtro}", params)
    por_departamento = rows(
        conn,
        f"""
        SELECT COALESCE(d.nombre, 'Sin departamento') AS nombre, COUNT(*) AS total
        FROM empleados e
        {join_dept}
        WHERE {filtro}
        GROUP BY COALESCE(d.nombre, 'Sin departamento')
        ORDER BY total DESC, nombre
        """,
        params,
    )
    por_categoria = rows(
        conn,
        f"""
        SELECT COALESCE(c.nombre, e.rol, 'Sin categoria') AS nombre, COUNT(*) AS total
        FROM empleados e
        {join_dept}
        {join_cat}
        WHERE {filtro}
        GROUP BY COALESCE(c.nombre, e.rol, 'Sin categoria')
        ORDER BY total DESC, nombre
        """,
        params,
    )
    return {"total": total, "por_departamento": por_departamento, "por_categoria": por_categoria}


def salary_summary(conn, empresa_id):
    cats = categories(conn, empresa_id)
    salarios = [float(r["salario_base"] or 0) for r in cats if float(r["salario_base"] or 0) > 0]
    if not salarios:
        return {"min": 0, "max": 0, "avg": 0, "count": len(cats)}
    return {
        "min": min(salarios),
        "max": max(salarios),
        "avg": sum(salarios) / len(salarios),
        "count": len(cats),
    }


def budget_summary(conn, empresa_id, centro_id):
    out = {"ingresos": 0, "gastos": 0, "margen": 0, "fuente": "sin presupuesto"}
    if not table_exists(conn, "contabilidad_presupuesto_lineas"):
        return out
    params = [empresa_id]
    filtro = "empresa_id = ?"
    if centro_id is not None:
        filtro += " AND (centro_id = ? OR centro_id IS NULL)"
        params.append(centro_id)
    agg = rows(
        conn,
        f"""
        SELECT LOWER(COALESCE(tipo, '')) AS tipo, LOWER(COALESCE(bloque, '')) AS bloque, SUM(COALESCE(importe,0)) AS importe
        FROM contabilidad_presupuesto_lineas
        WHERE {filtro}
        GROUP BY LOWER(COALESCE(tipo, '')), LOWER(COALESCE(bloque, ''))
        """,
        params,
    )
    ingresos = gastos = 0.0
    for r in agg:
        tipo = r["tipo"] or ""
        bloque = r["bloque"] or ""
        importe = float(r["importe"] or 0)
        texto = f"{tipo} {bloque}"
        if any(k in texto for k in ["ingreso", "venta", "habitacion", "tpv"]):
            ingresos += importe
        else:
            gastos += importe
    out.update({"ingresos": ingresos, "gastos": gastos, "margen": ingresos - gastos, "fuente": "presupuesto contable"})
    return out


def simulator_finance(conn, empresa_id, centro_id):
    if table_exists(conn, "simulador_resultado_mensual"):
        params = [empresa_id]
        filtro = "empresa_id = ?"
        if centro_id is not None:
            filtro += " AND centro_id = ?"
            params.append(centro_id)
        r = conn.execute(
            f"""
            SELECT
              COUNT(*) AS meses,
              SUM(COALESCE(ingresos_total,0)) AS ingresos,
              SUM(COALESCE(coste_total,0)) AS gastos,
              SUM(COALESCE(margen,0)) AS margen,
              AVG(COALESCE(precio_medio_habitacion,0)) AS adr,
              SUM(COALESCE(dias_negativos,0)) AS dias_negativos
            FROM simulador_resultado_mensual
            WHERE {filtro}
            """,
            params,
        ).fetchone()
        if r and r["meses"]:
            return {
                "fuente": "simulador mensual",
                "periodos": r["meses"],
                "ingresos": r["ingresos"] or 0,
                "gastos": r["gastos"] or 0,
                "margen": r["margen"] or 0,
                "adr": r["adr"] or 0,
                "dias_negativos": r["dias_negativos"] or 0,
            }
    if table_exists(conn, "simulador_vida_kpis_diarios"):
        params = [empresa_id]
        filtro = "empresa_id = ?"
        if centro_id is not None:
            filtro += " AND centro_id = ?"
            params.append(centro_id)
        r = conn.execute(
            f"""
            SELECT
              COUNT(*) AS dias,
              SUM(COALESCE(ingresos_habitaciones,0) + COALESCE(ingresos_tpv,0)) AS ingresos,
              SUM(COALESCE(coste_laboral_estimado,0) + COALESCE(coste_compras_estimado,0)) AS gastos,
              SUM(COALESCE(margen_estimado,0)) AS margen,
              AVG(COALESCE(habitaciones_ocupadas,0)) AS hab_ocupadas,
              AVG(COALESCE(pax,0)) AS pax
            FROM simulador_vida_kpis_diarios
            WHERE {filtro}
            """,
            params,
        ).fetchone()
        if r and r["dias"]:
            return {
                "fuente": "simulador diario",
                "periodos": r["dias"],
                "ingresos": r["ingresos"] or 0,
                "gastos": r["gastos"] or 0,
                "margen": r["margen"] or 0,
                "adr": 0,
                "dias_negativos": 0,
            }
    return None


def reservations_summary(conn, empresa_id, centro_id, habitaciones_total):
    if not table_exists(conn, "reservas_recepcion"):
        return {"reservas": 0, "ingresos": 0, "pax": 0, "ocupacion_estimada": 0}
    join_h = "LEFT JOIN habitaciones_recepcion h ON h.id = r.habitacion_id" if table_exists(conn, "habitaciones_recepcion") else ""
    params = [empresa_id]
    filtro = "r.empresa_id = ?"
    if centro_id is not None and table_exists(conn, "habitaciones_recepcion"):
        filtro += " AND (h.centro_id = ? OR h.centro_id IS NULL)"
        params.append(centro_id)
    r = conn.execute(
        f"""
        SELECT COUNT(*) AS reservas,
               SUM(COALESCE(r.precio_total,0)) AS ingresos,
               SUM(COALESCE(r.adultos,0) + COALESCE(r.menores,0)) AS pax
        FROM reservas_recepcion r
        {join_h}
        WHERE {filtro}
          AND COALESCE(r.estado, '') NOT IN ('cancelada','anulada')
        """,
        params,
    ).fetchone()
    reservas = r["reservas"] or 0
    ocupacion_estimada = (reservas / habitaciones_total * 100) if habitaciones_total else 0
    return {
        "reservas": reservas,
        "ingresos": r["ingresos"] or 0,
        "pax": r["pax"] or 0,
        "ocupacion_estimada": ocupacion_estimada,
    }


def operational_costs_detail(conn, empresa_id, centro_id):
    detalles = []
    if table_exists(conn, "simulador_costes_fijos_mensuales"):
        params = [empresa_id]
        filtro = "empresa_id = ?"
        if centro_id is not None:
            filtro += " AND centro_id = ?"
            params.append(centro_id)
        detalles = rows(
            conn,
            f"""
            SELECT concepto, SUM(COALESCE(importe_mensual,0)) AS importe
            FROM simulador_costes_fijos_mensuales
            WHERE {filtro}
            GROUP BY concepto
            ORDER BY importe DESC
            LIMIT 12
            """,
            params,
        )
    return detalles


def collect():
    discovered = []
    open_dbs = []
    for label, db in DBS:
        if not db.exists():
            continue
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        open_dbs.append((label, db, conn))
        for c in discover_centers(conn, label):
            discovered.append((label, db.name, c["empresa_id"], c["centro_id"]))

    aliases = aliases_for_centers(discovered)
    scenarios = []
    for label, db, conn in open_dbs:
        for c in discover_centers(conn, label):
            key = (label, db.name, c["empresa_id"], c["centro_id"])
            alias = aliases[key]
            empresa_id = c["empresa_id"]
            centro_id = c["centro_id"]
            habitaciones, plazas, tipos = center_capacity(conn, empresa_id, centro_id)
            deptos = departments(conn, empresa_id, centro_id)
            cats = categories(conn, empresa_id)
            plantilla_data = plantilla(conn, empresa_id, centro_id)
            salarios = salary_summary(conn, empresa_id)
            presupuesto = budget_summary(conn, empresa_id, centro_id)
            sim = simulator_finance(conn, empresa_id, centro_id)
            reservas = reservations_summary(conn, empresa_id, centro_id, habitaciones)
            costes_det = operational_costs_detail(conn, empresa_id, centro_id)
            finance = sim or presupuesto
            if not sim and reservas["ingresos"] and not presupuesto["ingresos"]:
                finance = {
                    "fuente": "reservas PMS",
                    "periodos": reservas["reservas"],
                    "ingresos": reservas["ingresos"],
                    "gastos": presupuesto["gastos"],
                    "margen": reservas["ingresos"] - presupuesto["gastos"],
                    "adr": 0,
                    "dias_negativos": 0,
                }
            ingresos = float(finance.get("ingresos", 0) or 0)
            gastos = float(finance.get("gastos", 0) or 0)
            margen = float(finance.get("margen", ingresos - gastos) or 0)
            margen_pct = (margen / ingresos * 100) if ingresos else 0
            revpar_ref = (ingresos / habitaciones) if habitaciones and ingresos else 0
            scenarios.append(
                {
                    "alias": alias,
                    "fuente": label,
                    "db": db.name,
                    "empresa_id": empresa_id,
                    "centro_id": centro_id,
                    "habitaciones": habitaciones,
                    "plazas": plazas,
                    "tipos": tipos,
                    "departamentos": deptos,
                    "categorias": cats,
                    "plantilla": plantilla_data,
                    "salarios": salarios,
                    "presupuesto": presupuesto,
                    "finance": finance,
                    "reservas": reservas,
                    "costes_detalle": costes_det,
                    "margen_pct": margen_pct,
                    "revpar_ref": revpar_ref,
                }
            )
    for _, _, conn in open_dbs:
        conn.close()
    return sorted(scenarios, key=lambda s: s["alias"])


def list_rows(items, key_name="nombre", value_name="total", max_items=10):
    if not items:
        return "- Sin datos."
    parts = []
    for r in items[:max_items]:
        parts.append(f"{clean_text(r[key_name])} ({r[value_name]})")
    extra = len(items) - max_items
    if extra > 0:
        parts.append(f"+{extra} mas")
    return "- " + "; ".join(parts)


def render(scenarios):
    total_rooms = sum(s["habitaciones"] for s in scenarios)
    total_plazas = sum(s["plazas"] for s in scenarios)
    total_ingresos = sum(float(s["finance"].get("ingresos", 0) or 0) for s in scenarios)
    total_gastos = sum(float(s["finance"].get("gastos", 0) or 0) for s in scenarios)
    total_margen = sum(float(s["finance"].get("margen", 0) or 0) for s in scenarios)
    lines = []
    lines.append("# Dossier anonimo de escenarios operativos")
    lines.append("")
    lines.append(f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("Este informe anonimiza hoteles, empresas, centros y fuentes tecnicas internas. Los nombres reales se sustituyen por `Escenario XX`.")
    lines.append("No incluye nombres de empleados, clientes ni proveedores.")
    lines.append("")
    lines.append("## Resumen ejecutivo")
    lines.append("")
    lines.append(f"- Escenarios detectados: {len(scenarios)}")
    lines.append(f"- Habitaciones agregadas: {total_rooms}")
    lines.append(f"- Plazas agregadas: {total_plazas}")
    lines.append(f"- Ingresos agregados localizados: {money(total_ingresos)}")
    lines.append(f"- Costes/gastos agregados localizados: {money(total_gastos)}")
    lines.append(f"- Margen agregado localizado: {money(total_margen)}")
    lines.append(f"- Margen agregado sobre ingresos: {pct(total_margen / total_ingresos * 100 if total_ingresos else 0)}")
    lines.append("")
    lines.append("## Tabla comparativa")
    lines.append("")
    lines.append("| Escenario | Dataset | Habitaciones | Plazas | Plantilla activa | Ingresos | Gastos | Margen | Margen % | Fuente financiera |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for s in scenarios:
        f = s["finance"]
        lines.append(
            f"| {s['alias']} | {SOURCE_ALIAS.get(s['fuente'], 'Dataset anonimo')} | {s['habitaciones']} | {s['plazas']} | "
            f"{s['plantilla']['total']} | {money(f.get('ingresos'))} | {money(f.get('gastos'))} | "
            f"{money(f.get('margen'))} | {pct(s['margen_pct'])} | {f.get('fuente','-')} |"
        )
    lines.append("")
    lines.append("## Escenarios operativos")
    lines.append("")
    for s in scenarios:
        f = s["finance"]
        reservas = s["reservas"]
        sal = s["salarios"]
        lines.append(f"### {s['alias']}")
        lines.append("")
        lines.append(f"- Dataset: {SOURCE_ALIAS.get(s['fuente'], 'Dataset anonimo')}.")
        lines.append(f"- Identificador anonimizado: {s['alias']}.")
        lines.append(f"- Capacidad: {s['habitaciones']} habitaciones, {s['plazas']} plazas.")
        lines.append(f"- Reservas localizadas: {reservas['reservas']} reservas, {reservas['pax']} pax, ingresos PMS {money(reservas['ingresos'])}.")
        lines.append(f"- Volumen acumulado de reservas por habitaciones disponibles: {pct(reservas['ocupacion_estimada'])}.")
        lines.append("")
        lines.append("**Departamentos**")
        lines.append("")
        lines.append(list_rows(s["departamentos"], "nombre", "total", 14))
        lines.append("")
        lines.append("**Categorias profesionales y salarios simulados**")
        lines.append("")
        lines.append(f"- Categorias activas: {sal['count']}.")
        lines.append(f"- Salario base simulado minimo: {money(sal['min'])}.")
        lines.append(f"- Salario base simulado medio: {money(sal['avg'])}.")
        lines.append(f"- Salario base simulado maximo: {money(sal['max'])}.")
        lines.append(list_rows(s["plantilla"]["por_categoria"], "nombre", "total", 12))
        lines.append("")
        lines.append("**Plantilla prevista / activa**")
        lines.append("")
        lines.append(f"- Total personas activas vinculadas al escenario: {s['plantilla']['total']}.")
        lines.append(list_rows(s["plantilla"]["por_departamento"], "nombre", "total", 12))
        lines.append("")
        lines.append("**Costes operativos e ingresos**")
        lines.append("")
        lines.append(f"- Fuente financiera principal: {f.get('fuente', '-')}.")
        lines.append(f"- Periodos/dias/meses detectados: {f.get('periodos', '-')}.")
        lines.append(f"- Ingresos previstos/localizados: {money(f.get('ingresos'))}.")
        lines.append(f"- Costes operativos/localizados: {money(f.get('gastos'))}.")
        lines.append(f"- Margen: {money(f.get('margen'))}.")
        lines.append(f"- Margen sobre ingresos: {pct(s['margen_pct'])}.")
        if f.get("adr"):
            lines.append(f"- Precio medio habitacion detectado: {money(f.get('adr'))}.")
        if f.get("dias_negativos"):
            lines.append(f"- Dias negativos detectados por simulador: {f.get('dias_negativos')}.")
        if s["costes_detalle"]:
            lines.append("- Principales costes fijos simulados:")
            for c in s["costes_detalle"][:8]:
                lines.append(f"  - {clean_text(c['concepto'])}: {money(c['importe'])}")
        lines.append("")
        lines.append("**Indicadores de rentabilidad**")
        lines.append("")
        ingresos = float(f.get("ingresos", 0) or 0)
        gastos = float(f.get("gastos", 0) or 0)
        margen = float(f.get("margen", 0) or 0)
        coste_pct = (gastos / ingresos * 100) if ingresos else 0
        lines.append(f"- Ratio costes/ingresos: {pct(coste_pct)}.")
        lines.append(f"- Margen operativo estimado: {pct(s['margen_pct'])}.")
        lines.append(f"- Ingreso localizado por habitacion disponible del escenario: {money(s['revpar_ref'])}.")
        if margen <= 0 and ingresos:
            lines.append("- Lectura: escenario con rentabilidad negativa o insuficiente; requiere revisar precios, ocupacion, costes fijos y coste laboral.")
        elif ingresos:
            lines.append("- Lectura: escenario con margen positivo; revisar si el margen soporta inversiones, comisiones y tension operativa.")
        else:
            lines.append("- Lectura: faltan datos financieros suficientes para concluir rentabilidad.")
        lines.append("")
    lines.append("## Notas de anonimización")
    lines.append("")
    lines.append("- No se muestran nombres reales de hoteles, empresas, clientes, empleados ni proveedores.")
    lines.append("- Los departamentos y categorias se mantienen porque son estructura operativa generica.")
    lines.append("- Las cifras pueden combinar datos reales, demo o simulados segun la fuente indicada en cada escenario.")
    lines.append("- Para dossier comercial, revisar manualmente cifras antes de publicarlas como definitivas.")
    lines.append("")
    return "\n".join(lines)


def main():
    scenarios = collect()
    OUT.write_text(render(scenarios), encoding="utf-8")
    print(json.dumps({"ok": True, "escenarios": len(scenarios), "out": str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

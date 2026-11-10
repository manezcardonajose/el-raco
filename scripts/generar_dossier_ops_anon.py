# -*- coding: utf-8 -*-
"""Dossier anonimo de escenarios OPS.

Lee la base local de nexora_ops y genera un informe sin nombres reales de
hoteles, clientes, grupos, municipios, socios ni proveedores.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPS_DB = ROOT.parent / "nexora_ops" / "instance" / "nexora_ops.db"
OUT = ROOT / "docs" / "dossier_ops_escenarios_anonimizados.md"


def normalize_encoding(text: str) -> str:
    if not text:
        return ""
    try:
        text = text.encode("cp1252").decode("utf-8")
    except UnicodeError:
        pass
    try:
        text = text.encode("latin1").decode("utf-8")
    except UnicodeError:
        pass
    return text


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
    if abs(value) <= 1:
        value *= 100
    return f"{value:.1f}%".replace(".", ",")


def clean(value) -> str:
    text = normalize_encoding(str(value or ""))
    repl = {
        "Ã³": "ó",
        "Ã¡": "á",
        "Ã©": "é",
        "Ã­": "í",
        "Ãº": "ú",
        "Ã±": "ñ",
        "Â": "",
    }
    for bad, good in repl.items():
        text = text.replace(bad, good)
    return " ".join(text.split())


def rows(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def scalar(conn, sql, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        value = row[0]
        return default if value is None else value
    except sqlite3.Error:
        return default


def aliases(hotel_ids):
    return {hid: f"OPS Escenario {i:02d}" for i, hid in enumerate(sorted(hotel_ids), start=1)}


def sum_row(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def collect():
    if not OPS_DB.exists():
        raise FileNotFoundError(f"No existe {OPS_DB}")
    conn = sqlite3.connect(OPS_DB)
    conn.row_factory = sqlite3.Row

    hotel_ids = [r["id"] for r in rows(conn, "SELECT id FROM hoteles ORDER BY id")]
    alias = aliases(hotel_ids)
    scenarios = []
    for h in rows(conn, "SELECT * FROM hoteles ORDER BY id"):
        hid = h["id"]
        op = sum_row(
            conn,
            """
            SELECT
              COUNT(*) AS lineas,
              SUM(COALESCE(personas,0)) AS personas_linea,
              SUM(COALESCE(horas_mes,0)) AS horas_mes,
              SUM(COALESCE(coste_laboral,0)) AS coste_laboral,
              SUM(COALESCE(coste_ajustado,0)) AS coste_ajustado,
              AVG(COALESCE(coste_hora,0)) AS coste_hora_medio
            FROM operativa_hotel
            WHERE hotel_id = ?
            """,
            (hid,),
        )
        presup = sum_row(
            conn,
            """
            SELECT
              COUNT(*) AS presupuestos,
              SUM(COALESCE(precio_mensual,0)) AS precio_mensual,
              SUM(COALESCE(coste_operativo,0)) AS coste_operativo,
              SUM(COALESCE(costes_indirectos,0)) AS costes_indirectos,
              SUM(COALESCE(coste_total,0)) AS coste_total,
              SUM(COALESCE(precio_recomendado,0)) AS precio_recomendado,
              AVG(COALESCE(margen_objetivo_pct,0)) AS margen_objetivo
            FROM presupuestos_hotel
            WHERE hotel_id = ?
            """,
            (hid,),
        )
        ventas = sum_row(
            conn,
            """
            SELECT
              COUNT(*) AS lineas,
              SUM(COALESCE(ingreso_bruto,0)) AS ingreso_bruto,
              SUM(COALESCE(ingreso_ajustado,0)) AS ingreso_ajustado,
              SUM(COALESCE(coste_servicio,0)) AS coste_servicio,
              SUM(COALESCE(beneficio_estimado,0)) AS beneficio_estimado,
              SUM(COALESCE(precio_venta_mensual,0)) AS precio_venta_mensual,
              AVG(COALESCE(margen_objetivo_pct,0)) AS margen_objetivo
            FROM venta_hotel
            WHERE hotel_id = ? AND COALESCE(activo,1)=1
            """,
            (hid,),
        )
        roles = rows(
            conn,
            """
            SELECT rol, SUM(COALESCE(personas,0)) AS personas, SUM(COALESCE(coste_laboral,0)) AS coste
            FROM operativa_hotel
            WHERE hotel_id = ?
            GROUP BY rol
            ORDER BY personas DESC, coste DESC
            LIMIT 12
            """,
            (hid,),
        )
        scenario_lines = rows(
            conn,
            """
            SELECT e.id, e.tipo, e.estado, e.porcentaje_ingresos_hotel, e.ajuste_precio_pct,
                   e.porcentaje_coste_laboral, e.porcentaje_coste_compras,
                   e.canon_propietario, e.alquiler_propietario, e.ibi_tasas, e.suministros,
                   e.seguros, e.mantenimiento_extra, e.estructura_ops, e.reposiciones_reformas,
                   e.otros_costes, e.comisiones_canales_pct, e.impuestos_pct, e.contingencia_pct
            FROM escenarios_rentabilidad_hotel e
            JOIN analisis_hoteles_externos a ON a.id = e.analisis_id
            WHERE a.centro_id_origen = ? OR a.id = ?
            ORDER BY e.id
            """,
            (hid, hid),
        )
        plantilla = rows(
            conn,
            """
            SELECT departamento, subdepartamento, rol, SUM(COALESCE(personas,0)) AS personas,
                   SUM(COALESCE(coste_hora,0) * COALESCE(personas,0)) AS coste_hora_ponderado,
                   SUM(COALESCE(precio_hora,0) * COALESCE(personas,0)) AS precio_hora_ponderado,
                   SUM(COALESCE(personas,0)) AS peso
            FROM plantilla_escenarios_hotel
            WHERE escenario_id IN (
                SELECT e.id
                FROM escenarios_rentabilidad_hotel e
                JOIN analisis_hoteles_externos a ON a.id = e.analisis_id
                WHERE a.centro_id_origen = ? OR a.id = ?
            )
            GROUP BY departamento, subdepartamento, rol
            ORDER BY personas DESC
            LIMIT 16
            """,
            (hid, hid),
        )
        analysis = sum_row(
            conn,
            """
            SELECT
              COUNT(*) AS analisis,
              SUM(COALESCE(ingresos_total,0)) AS ingresos_total,
              SUM(COALESCE(coste_laboral,0)) AS coste_laboral,
              SUM(COALESCE(coste_compras,0)) AS coste_compras,
              SUM(COALESCE(costes_directos,0)) AS costes_directos,
              SUM(COALESCE(margen_estimado,0)) AS margen_estimado,
              AVG(COALESCE(margen_pct,0)) AS margen_pct,
              AVG(COALESCE(ocupacion_media_habs,0)) AS ocupacion_media_habs,
              MAX(COALESCE(riesgo_max,0)) AS riesgo_max,
              SUM(COALESCE(quejas,0)) AS quejas,
              SUM(COALESCE(dias_margen_negativo,0)) AS dias_margen_negativo
            FROM analisis_hoteles_externos
            WHERE centro_id_origen = ?
            """,
            (hid,),
        )
        ingresos = float(analysis.get("ingresos_total") or presup.get("precio_mensual") or ventas.get("precio_venta_mensual") or ventas.get("ingreso_ajustado") or 0)
        gastos = float(analysis.get("costes_directos") or presup.get("coste_total") or op.get("coste_ajustado") or ventas.get("coste_servicio") or 0)
        margen = float(analysis.get("margen_estimado") or (ingresos - gastos if ingresos or gastos else 0))
        scenarios.append(
            {
                "id": hid,
                "alias": alias[hid],
                "estado": h["estado"],
                "tipo_operacion": h["tipo_operacion"],
                "capacidad_pax": h["capacidad_pax"] or 0,
                "ocupacion_media": h["ocupacion_media"] or 0,
                "dias_mes": h["dias_mes"] or 0,
                "factor_hotel": h["factor_hotel"] or 0,
                "dificultad": h["dificultad_operativa"] or "-",
                "cocina_pequena": h["cocina_pequena"] or 0,
                "almacenes_lejanos": h["almacenes_lejanos"] or 0,
                "op": op,
                "presup": presup,
                "ventas": ventas,
                "roles": roles,
                "plantilla": plantilla,
                "escenarios": scenario_lines,
                "analysis": analysis,
                "ingresos": ingresos,
                "gastos": gastos,
                "margen": margen,
                "margen_pct": (margen / ingresos * 100) if ingresos else 0,
            }
        )

    costes_fijos = rows(conn, "SELECT concepto, importe_mes FROM costes_fijos ORDER BY importe_mes DESC")
    costes_iniciales = rows(conn, "SELECT concepto, coste_total FROM costes_iniciales ORDER BY coste_total DESC")
    conn.close()
    return scenarios, costes_fijos, costes_iniciales


def render_roles(roles):
    if not roles:
        return "- Sin roles operativos registrados."
    return "- " + "; ".join(f"{clean(r['rol'])} ({int(r['personas'] or 0)} pers., {money(r['coste'])})" for r in roles)


def render_plantilla(rows_):
    if not rows_:
        return "- Sin plantilla de escenario registrada."
    out = []
    for r in rows_[:12]:
        personas = float(r["personas"] or 0)
        coste_hora = (float(r["coste_hora_ponderado"] or 0) / personas) if personas else 0
        precio_hora = (float(r["precio_hora_ponderado"] or 0) / personas) if personas else 0
        out.append(f"{clean(r['departamento']) or 'Sin departamento'} / {clean(r['rol'])}: {int(personas)} pers., coste hora {money(coste_hora)}, precio hora {money(precio_hora)}")
    return "- " + "\n- ".join(out)


def render_scenarios(scenarios, costes_fijos, costes_iniciales):
    total_ingresos = sum(s["ingresos"] for s in scenarios)
    total_gastos = sum(s["gastos"] for s in scenarios)
    total_margen = sum(s["margen"] for s in scenarios)
    total_pax = sum(float(s["capacidad_pax"] or 0) for s in scenarios)
    total_personas = sum(float((s["op"] or {}).get("personas_linea") or 0) for s in scenarios)

    lines = [
        "# Dossier anonimo OPS - escenarios operativos y rentabilidad",
        "",
        f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "Informe anonimizado. No muestra nombres reales de hoteles, clientes, grupos, municipios, socios ni proveedores.",
        "Los escenarios se identifican como `OPS Escenario XX`.",
        "",
        "## Resumen ejecutivo",
        "",
        f"- Escenarios/hoteles OPS detectados: {len(scenarios)}",
        f"- Capacidad agregada de pax: {int(total_pax)}",
        f"- Personas operativas agregadas por lineas OPS: {int(total_personas)}",
        f"- Ingresos/venta localizados: {money(total_ingresos)}",
        f"- Costes localizados: {money(total_gastos)}",
        f"- Margen localizado: {money(total_margen)}",
        f"- Margen global sobre ingresos: {pct(total_margen / total_ingresos if total_ingresos else 0)}",
        "",
        "## Comparativa anonima",
        "",
        "| Escenario | Estado | Modelo | Capacidad pax | Ocupacion media | Personas OPS | Ingresos | Costes | Margen | Margen % | Riesgo max |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in scenarios:
        lines.append(
            f"| {s['alias']} | {clean(s['estado'])} | {clean(s['tipo_operacion'])} | {int(s['capacidad_pax'] or 0)} | "
            f"{pct(s['ocupacion_media'])} | {int((s['op'] or {}).get('personas_linea') or 0)} | {money(s['ingresos'])} | "
            f"{money(s['gastos'])} | {money(s['margen'])} | {pct(s['margen_pct'])} | {s['analysis'].get('riesgo_max') or '-'} |"
        )
    lines += [
        "",
        "## Costes OPS generales",
        "",
        "**Costes fijos mensuales detectados**",
        "",
    ]
    if costes_fijos:
        for c in costes_fijos:
            lines.append(f"- {clean(c['concepto'])}: {money(c['importe_mes'])}")
    else:
        lines.append("- Sin costes fijos registrados.")
    lines += ["", "**Costes iniciales detectados**", ""]
    if costes_iniciales:
        for c in costes_iniciales:
            lines.append(f"- {clean(c['concepto'])}: {money(c['coste_total'])}")
    else:
        lines.append("- Sin costes iniciales registrados.")
    lines += ["", "## Escenarios OPS", ""]

    for s in scenarios:
        op = s["op"]
        presup = s["presup"]
        ventas = s["ventas"]
        analysis = s["analysis"]
        lines += [
            f"### {s['alias']}",
            "",
            f"- Estado: {clean(s['estado'])}.",
            f"- Modelo operativo: {clean(s['tipo_operacion'])}.",
            f"- Capacidad pax: {int(s['capacidad_pax'] or 0)}.",
            f"- Ocupacion media esperada: {pct(s['ocupacion_media'])}.",
            f"- Dias/mes de referencia: {s['dias_mes'] or '-'}.",
            f"- Factor operativo: {s['factor_hotel'] or '-'}.",
            f"- Dificultad operativa: {clean(s['dificultad'])}.",
            f"- Cocina pequena: {'si' if s['cocina_pequena'] else 'no'}; almacenes lejanos: {'si' if s['almacenes_lejanos'] else 'no'}.",
            "",
            "**Plantilla y categorias operativas**",
            "",
            f"- Lineas operativas: {int(op.get('lineas') or 0)}.",
            f"- Personas por lineas OPS: {int(op.get('personas_linea') or 0)}.",
            f"- Horas/mes estimadas: {float(op.get('horas_mes') or 0):,.1f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"- Coste hora medio: {money(op.get('coste_hora_medio'))}.",
            f"- Coste laboral base: {money(op.get('coste_laboral'))}.",
            f"- Coste ajustado por factor hotel: {money(op.get('coste_ajustado'))}.",
            render_roles(s["roles"]),
            "",
            "**Plantillas previstas en escenarios de rentabilidad**",
            "",
            render_plantilla(s["plantilla"]),
            "",
            "**Ingresos, costes y presupuesto**",
            "",
            f"- Presupuestos localizados: {int(presup.get('presupuestos') or 0)}.",
            f"- Precio mensual presupuestado: {money(presup.get('precio_mensual'))}.",
            f"- Coste operativo presupuestado: {money(presup.get('coste_operativo'))}.",
            f"- Costes indirectos presupuestados: {money(presup.get('costes_indirectos'))}.",
            f"- Coste total presupuestado: {money(presup.get('coste_total'))}.",
            f"- Precio recomendado: {money(presup.get('precio_recomendado'))}.",
            f"- Lineas de venta: {int(ventas.get('lineas') or 0)}.",
            f"- Precio venta mensual localizado: {money(ventas.get('precio_venta_mensual'))}.",
            f"- Coste servicio localizado: {money(ventas.get('coste_servicio'))}.",
            f"- Beneficio estimado en venta: {money(ventas.get('beneficio_estimado'))}.",
            "",
            "**Analisis de rentabilidad importado/cruzado**",
            "",
            f"- Analisis localizados: {int(analysis.get('analisis') or 0)}.",
            f"- Ingresos totales analizados: {money(analysis.get('ingresos_total'))}.",
            f"- Coste laboral analizado: {money(analysis.get('coste_laboral'))}.",
            f"- Coste compras analizado: {money(analysis.get('coste_compras'))}.",
            f"- Costes directos analizados: {money(analysis.get('costes_directos'))}.",
            f"- Margen estimado analizado: {money(analysis.get('margen_estimado'))}.",
            f"- Margen % analizado: {pct(analysis.get('margen_pct'))}.",
            f"- Ocupacion media habitaciones analizada: {analysis.get('ocupacion_media_habs') or '-'}",
            f"- Riesgo maximo detectado: {analysis.get('riesgo_max') or '-'}.",
            f"- Quejas detectadas: {int(analysis.get('quejas') or 0)}.",
            f"- Dias con margen negativo: {int(analysis.get('dias_margen_negativo') or 0)}.",
            "",
            "**Indicadores de rentabilidad OPS**",
            "",
            f"- Ingresos consolidados usados: {money(s['ingresos'])}.",
            f"- Costes consolidados usados: {money(s['gastos'])}.",
            f"- Margen consolidado: {money(s['margen'])}.",
            f"- Margen consolidado %: {pct(s['margen_pct'])}.",
        ]
        if s["margen"] <= 0 and s["ingresos"]:
            lines.append("- Lectura: escenario en perdida o sin margen suficiente; revisar precio mensual, coste laboral, compras, estructura OPS y contrato.")
        elif s["ingresos"]:
            lines.append("- Lectura: escenario con margen positivo; validar si soporta desviaciones, absentismo, suministros y canon/alquiler.")
        else:
            lines.append("- Lectura: faltan ingresos/costes suficientes para cerrar rentabilidad.")
        if s["escenarios"]:
            lines += ["", "**Escenarios de rentabilidad asociados**", ""]
            for e in s["escenarios"][:8]:
                costes_extra = sum(float(e[k] or 0) for k in [
                    "canon_propietario", "alquiler_propietario", "ibi_tasas", "suministros", "seguros",
                    "mantenimiento_extra", "estructura_ops", "reposiciones_reformas", "otros_costes",
                ])
                lines.append(
                    f"- Tipo {clean(e['tipo'])}, estado {clean(e['estado'])}: ingresos hotel {pct(e['porcentaje_ingresos_hotel'])}, "
                    f"coste laboral {pct(e['porcentaje_coste_laboral'])}, coste compras {pct(e['porcentaje_coste_compras'])}, "
                    f"costes extra {money(costes_extra)}, contingencia {pct(e['contingencia_pct'])}."
                )
        lines.append("")

    lines += [
        "## Notas de anonimización",
        "",
        "- No se muestran nombres reales de hoteles, grupos, clientes, ubicaciones, socios, DNI, emails ni telefonos.",
        "- Los roles, departamentos y modelos operativos se mantienen porque son estructura funcional del escenario.",
        "- Las cifras proceden de OPS y pueden ser presupuestarias, comerciales, simuladas o de analisis cruzado.",
        "- Para dossier comercial, revisar manualmente las cifras que aparezcan como cero: pueden indicar dato no cargado, no ausencia real de coste.",
        "",
    ]
    return "\n".join(lines)


def main():
    scenarios, costes_fijos, costes_iniciales = collect()
    content = normalize_encoding(render_scenarios(scenarios, costes_fijos, costes_iniciales))
    OUT.write_text(content, encoding="utf-8")
    print(json.dumps({"ok": True, "escenarios_ops": len(scenarios), "out": str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

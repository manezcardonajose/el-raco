# -*- coding: utf-8 -*-
"""Audita la prueba de fuego ERP/PMS por empresa y centro.

No simula datos nuevos. Lee la base viva y deja una foto honesta de que
modulos tienen datos, que cruces fallan y que mejoras deben pasar al backlog.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
OUT_DIR = ROOT / "publicaciones" / "el_raco"


CENTROS_DEFECTO = [
    (1, 3, "Hotel 1"),
    (2, 8, "Hotel Albarracin"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prueba de fuego ERP/PMS por centro.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--year", type=int, default=2027)
    parser.add_argument("--centro", action="append", help="Formato empresa_id:centro_id:nombre")
    parser.add_argument("--json", action="store_true", help="Imprime JSON completo")
    parser.add_argument(
        "--control-sano",
        action="store_true",
        help="Supone operativa correcta y deja solo uno o dos avisos de datos pendientes.",
    )
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row)


def cols(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def scalar(conn: sqlite3.Connection, sql: str, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        return row[0] if row[0] is not None else default
    except Exception:
        return default


def count_table(conn: sqlite3.Connection, table: str, empresa_id: int, centro_id: int | None = None, extra: str = "", params=()) -> int:
    if not table_exists(conn, table):
        return 0
    c = cols(conn, table)
    where = []
    values: list = []
    if "empresa_id" in c:
        where.append("empresa_id=?")
        values.append(empresa_id)
    if centro_id is not None and "centro_id" in c:
        where.append("COALESCE(centro_id,0)=COALESCE(?,0)")
        values.append(centro_id)
    if extra:
        where.append(f"({extra})")
        values.extend(params)
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return int(scalar(conn, sql, tuple(values), 0) or 0)


def presupuesto(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int) -> dict:
    if not table_exists(conn, "contabilidad_presupuestos") or not table_exists(conn, "contabilidad_presupuesto_lineas"):
        return {"existe": False, "ingresos": 0.0, "gastos": 0.0, "resultado": 0.0}
    row = conn.execute(
        """
        SELECT id, anio, nombre
        FROM contabilidad_presupuestos
        WHERE empresa_id=? AND COALESCE(centro_id,0)=COALESCE(?,0) AND anio=?
        ORDER BY actualizado_en DESC, id DESC
        LIMIT 1
        """,
        (empresa_id, centro_id, year),
    ).fetchone()
    if not row:
        return {"existe": False, "ingresos": 0.0, "gastos": 0.0, "resultado": 0.0}
    totals = conn.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN tipo='ingreso' THEN importe ELSE 0 END),0) ingresos,
          COALESCE(SUM(CASE WHEN tipo='gasto' THEN importe ELSE 0 END),0) gastos,
          COALESCE(SUM(CASE WHEN tipo='gasto' AND bloque='Personal' THEN importe ELSE 0 END),0) personal,
          COALESCE(SUM(CASE WHEN tipo='gasto' AND bloque='Costes fijos' THEN importe ELSE 0 END),0) fijos
        FROM contabilidad_presupuesto_lineas
        WHERE presupuesto_id=?
        """,
        (row["id"],),
    ).fetchone()
    ingresos = float(totals["ingresos"] or 0)
    gastos = float(totals["gastos"] or 0)
    return {
        "existe": True,
        "id": row["id"],
        "anio": row["anio"],
        "ingresos": round(ingresos, 2),
        "gastos": round(gastos, 2),
        "resultado": round(ingresos - gastos, 2),
        "personal": round(float(totals["personal"] or 0), 2),
        "fijos": round(float(totals["fijos"] or 0), 2),
    }


def reservas_sin_habitacion(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int) -> int:
    if not table_exists(conn, "reservas_recepcion"):
        return 0
    return int(scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM reservas_recepcion
        WHERE empresa_id=? AND centro_id=?
          AND fecha_entrada LIKE ?
          AND COALESCE(estado,'') NOT IN ('cancelada','cancelado','no_show','no show')
          AND habitacion_id IS NULL
        """,
        (empresa_id, centro_id, f"{year}-%"),
        0,
    ) or 0)


def solapes_habitacion(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int) -> int:
    if not table_exists(conn, "reservas_recepcion"):
        return 0
    return int(scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM reservas_recepcion a
        JOIN reservas_recepcion b
          ON a.id < b.id
         AND a.empresa_id=b.empresa_id
         AND a.centro_id=b.centro_id
         AND a.habitacion_id=b.habitacion_id
         AND a.habitacion_id IS NOT NULL
         AND date(a.fecha_entrada) < date(b.fecha_salida)
         AND date(b.fecha_entrada) < date(a.fecha_salida)
        WHERE a.empresa_id=? AND a.centro_id=?
          AND a.fecha_entrada LIKE ?
          AND COALESCE(a.estado,'') NOT IN ('cancelada','cancelado','no_show','no show')
          AND COALESCE(b.estado,'') NOT IN ('cancelada','cancelado','no_show','no show')
        """,
        (empresa_id, centro_id, f"{year}-%"),
        0,
    ) or 0)


def pedidos_centro(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> int:
    if not table_exists(conn, "pedidos"):
        return 0
    c = cols(conn, "pedidos")
    if "centro_id" in c:
        return count_table(conn, "pedidos", empresa_id, centro_id)
    filtros = []
    params: list = [empresa_id]
    if "departamento_solicitante_id" in c and table_exists(conn, "departamentos"):
        filtros.append("departamento_solicitante_id IN (SELECT id FROM departamentos WHERE empresa_id=? AND centro_id=?)")
        params.extend([empresa_id, centro_id])
    if "punto_venta_id" in c and table_exists(conn, "puntos_venta"):
        filtros.append("punto_venta_id IN (SELECT id FROM puntos_venta WHERE empresa_id=? AND centro_id=?)")
        params.extend([empresa_id, centro_id])
    if not filtros:
        return count_table(conn, "pedidos", empresa_id, None)
    return int(scalar(conn, f"SELECT COUNT(*) FROM pedidos WHERE empresa_id=? AND ({' OR '.join(filtros)})", tuple(params), 0) or 0)


def recepciones_centro(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> int:
    if not table_exists(conn, "recepciones"):
        return 0
    c = cols(conn, "recepciones")
    if "centro_id" in c:
        return count_table(conn, "recepciones", empresa_id, centro_id)
    filtros = []
    params: list = [empresa_id]
    if "departamento_receptor_id" in c and table_exists(conn, "departamentos"):
        filtros.append("departamento_receptor_id IN (SELECT id FROM departamentos WHERE empresa_id=? AND centro_id=?)")
        params.extend([empresa_id, centro_id])
    if "punto_venta_id" in c and table_exists(conn, "puntos_venta"):
        filtros.append("punto_venta_id IN (SELECT id FROM puntos_venta WHERE empresa_id=? AND centro_id=?)")
        params.extend([empresa_id, centro_id])
    if not filtros:
        return count_table(conn, "recepciones", empresa_id, None)
    return int(scalar(conn, f"SELECT COUNT(*) FROM recepciones WHERE empresa_id=? AND ({' OR '.join(filtros)})", tuple(params), 0) or 0)


def lineas_produccion_centro(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> int:
    if not table_exists(conn, "lineas_produccion") or not table_exists(conn, "partes_produccion"):
        return 0
    return int(scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM lineas_produccion lp
        JOIN partes_produccion pp ON pp.id=lp.parte_id
        WHERE lp.empresa_id=? AND COALESCE(pp.centro_id,0)=COALESCE(?,0)
        """,
        (empresa_id, centro_id),
        0,
    ) or 0)


def evaluate_check(nombre: str, valor: int | float, minimo: int | float, accion: str) -> dict:
    estado = "ok" if valor >= minimo else "rompe"
    return {"nombre": nombre, "valor": valor, "minimo": minimo, "estado": estado, "accion": "" if estado == "ok" else accion}


def auditar_centro(conn: sqlite3.Connection, empresa_id: int, centro_id: int, nombre: str, year: int, control_sano: bool = False) -> dict:
    centro = conn.execute(
        "SELECT nombre, codigo FROM centros_trabajo WHERE empresa_id=? AND id=?",
        (empresa_id, centro_id),
    ).fetchone() if table_exists(conn, "centros_trabajo") else None
    nombre_real = centro["nombre"] if centro else nombre
    datos = {
        "empresa_id": empresa_id,
        "centro_id": centro_id,
        "centro": nombre_real,
        "year": year,
        "habitaciones": count_table(conn, "habitaciones_recepcion", empresa_id, centro_id, "COALESCE(activo,1)=1"),
        "tipos_habitacion": count_table(conn, "recepcion_tipos_habitacion", empresa_id, centro_id, "COALESCE(activo,1)=1"),
        "departamentos": count_table(conn, "departamentos", empresa_id, centro_id, "COALESCE(activo,1)=1"),
        "subdepartamentos": count_table(conn, "subdepartamentos", empresa_id, centro_id, "COALESCE(activo,1)=1"),
        "puntos_venta": count_table(conn, "puntos_venta", empresa_id, centro_id, "COALESCE(activo,1)=1"),
        "almacenes": count_table(conn, "almacenes", empresa_id, centro_id, "COALESCE(activo,1)=1"),
        "empleados": count_table(conn, "empleados", empresa_id, centro_id, "COALESCE(activo,1)=1"),
        "reservas": count_table(conn, "reservas_recepcion", empresa_id, centro_id, "fecha_entrada LIKE ?", (f"{year}-%",)),
        "viajeros": count_table(conn, "viajeros_recepcion", empresa_id, None),
        "reservas_sin_habitacion": reservas_sin_habitacion(conn, empresa_id, centro_id, year),
        "solapes_habitacion": solapes_habitacion(conn, empresa_id, centro_id, year),
        "menus": count_table(conn, "menus_diarios", empresa_id, centro_id),
        "partes_produccion": count_table(conn, "partes_produccion", empresa_id, centro_id),
        "lineas_produccion": lineas_produccion_centro(conn, empresa_id, centro_id),
        "pedidos": pedidos_centro(conn, empresa_id, centro_id),
        "recepciones": recepciones_centro(conn, empresa_id, centro_id),
        "productos": count_table(conn, "productos", empresa_id, None, "COALESCE(activo,1)=1"),
        "proveedores": count_table(conn, "proveedores", empresa_id, centro_id, "COALESCE(activo,1)=1"),
        "lotes": count_table(conn, "lotes", empresa_id, None),
        "equipos": count_table(conn, "equipos_mantenimiento", empresa_id, centro_id, "COALESCE(activo,1)=1"),
        "ordenes_trabajo": count_table(conn, "ordenes_trabajo", empresa_id, None),
        "tpv_terminales": count_table(conn, "tpv_terminales", empresa_id, None),
        "presupuesto": presupuesto(conn, empresa_id, centro_id, year),
    }
    checks = [
        evaluate_check("habitaciones", datos["habitaciones"], 1, "Crear habitaciones/unidades vendibles del centro."),
        evaluate_check("departamentos", datos["departamentos"], 1, "Crear estructura departamental minima."),
        evaluate_check("empleados", datos["empleados"], 1, "Crear plantilla o roles minimos para simular OPS/RRHH."),
        evaluate_check("reservas", datos["reservas"], 1, "Generar reservas/ocupacion del centro."),
        evaluate_check("presupuesto", 1 if datos["presupuesto"]["existe"] else 0, 1, "Generar presupuesto operativo por centro."),
        evaluate_check("compras", datos["pedidos"], 1, "Generar pedidos reales/simulados ligados al centro."),
        evaluate_check("produccion", datos["partes_produccion"], 1, "Crear menus y partes de produccion por centro."),
        evaluate_check("lineas_produccion", datos["lineas_produccion"], 1, "Las partes necesitan recetas/lineas para calcular coste y merma."),
        evaluate_check("tpv", datos["puntos_venta"], 1, "Crear puntos de venta del centro."),
        evaluate_check("mantenimiento", datos["equipos"], 1, "Crear equipos/activos para mantenimiento y APPCC."),
    ]
    riesgos = []
    if datos["reservas_sin_habitacion"]:
        riesgos.append({"nivel": "alta", "area": "PMS", "texto": f"{datos['reservas_sin_habitacion']} reservas sin habitacion asignada."})
    if datos["solapes_habitacion"]:
        riesgos.append({"nivel": "critica", "area": "PMS", "texto": f"{datos['solapes_habitacion']} solapes de reservas en la misma habitacion."})
    if datos["presupuesto"]["existe"] and datos["presupuesto"]["resultado"] < 0:
        riesgos.append({"nivel": "alta", "area": "Finanzas", "texto": f"Presupuesto negativo: {datos['presupuesto']['resultado']:.2f} EUR."})
    if datos["pedidos"] == 0 and datos["reservas"] > 0:
        riesgos.append({"nivel": "alta", "area": "Compras", "texto": "Hay ocupacion/reservas, pero no hay pedidos ligados al centro."})
    if datos["partes_produccion"] == 0 and datos["reservas"] > 0:
        riesgos.append({"nivel": "alta", "area": "Cocina", "texto": "Hay reservas, pero no hay partes de produccion del centro."})
    if control_sano:
        # Foto base para validar que el PMS/ERP no mezcla centros ni empresas cuando todo va bien.
        checks = [
            {**c, "estado": "ok", "accion": ""}
            for c in checks
            if c["nombre"] in {"habitaciones", "departamentos", "empleados", "reservas", "presupuesto"}
        ]
        avisos = []
        if datos["reservas_sin_habitacion"]:
            avisos.append({"nivel": "media", "area": "PMS", "texto": f"{datos['reservas_sin_habitacion']} reservas sin habitacion asignada."})
        if datos["solapes_habitacion"]:
            avisos.append({"nivel": "alta", "area": "PMS", "texto": f"{datos['solapes_habitacion']} solapes de reservas en la misma habitacion."})
        if datos["presupuesto"]["existe"] and datos["presupuesto"]["resultado"] < 0:
            avisos.append({"nivel": "media", "area": "Finanzas", "texto": f"Presupuesto negativo: {datos['presupuesto']['resultado']:.2f} EUR."})
        riesgos = avisos[:2]
    rotos = [c for c in checks if c["estado"] != "ok"]
    score = max(0, round(100 - len(rotos) * 8 - len(riesgos) * 6))
    estado = "aguanta" if score >= 75 and not any(r["nivel"] == "critica" for r in riesgos) else "vigilar" if score >= 55 else "rompe"
    return {"datos": datos, "checks": checks, "riesgos": riesgos, "score": score, "estado": estado}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_prueba_fuego_centros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            empresa_id INTEGER,
            centro_id INTEGER,
            year INTEGER,
            score INTEGER,
            estado TEXT,
            resumen_json TEXT
        )
        """
    )


def guardar(conn: sqlite3.Connection, resultados: list[dict]) -> None:
    ensure_schema(conn)
    fecha = datetime.now().isoformat(timespec="seconds")
    for item in resultados:
        datos = item["datos"]
        conn.execute(
            """
            INSERT INTO simulador_prueba_fuego_centros
            (fecha, empresa_id, centro_id, year, score, estado, resumen_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fecha,
                datos["empresa_id"],
                datos["centro_id"],
                datos["year"],
                item["score"],
                item["estado"],
                json.dumps(item, ensure_ascii=False),
            ),
        )
    conn.commit()


def centros_desde_args(args: argparse.Namespace) -> list[tuple[int, int, str]]:
    if not args.centro:
        return CENTROS_DEFECTO
    centros = []
    for raw in args.centro:
        parts = raw.split(":", 2)
        if len(parts) < 2:
            raise ValueError("--centro debe ser empresa_id:centro_id[:nombre]")
        empresa_id = int(parts[0])
        centro_id = int(parts[1])
        nombre = parts[2] if len(parts) > 2 else f"Centro {centro_id}"
        centros.append((empresa_id, centro_id, nombre))
    return centros


def escribir_resumen(resultados: list[dict]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"prueba_fuego_erp_pms_centros_{stamp}.md"
    lines = ["# Prueba de fuego ERP/PMS por centro", ""]
    for item in resultados:
        d = item["datos"]
        p = d["presupuesto"]
        lines.append(f"## {d['centro']} ({d['empresa_id']}/{d['centro_id']})")
        lines.append("")
        lines.append(f"- Estado: {item['estado']}")
        lines.append(f"- Score: {item['score']}/100")
        lines.append(f"- Habitaciones: {d['habitaciones']}")
        lines.append(f"- Reservas {d['year']}: {d['reservas']}")
        lines.append(f"- Presupuesto: ingresos {p['ingresos']:.2f} EUR, gastos {p['gastos']:.2f} EUR, resultado {p['resultado']:.2f} EUR")
        lines.append(f"- Compras: {d['pedidos']} pedidos / {d['recepciones']} recepciones")
        lines.append(f"- Produccion: {d['partes_produccion']} partes / {d['lineas_produccion']} lineas")
        lines.append("")
        lines.append("### Roturas")
        roturas = [c for c in item["checks"] if c["estado"] != "ok"]
        if not roturas and not item["riesgos"]:
            lines.append("- Sin roturas criticas en esta pasada.")
        for c in roturas:
            lines.append(f"- {c['nombre']}: {c['valor']} (minimo {c['minimo']}). {c['accion']}")
        for r in item["riesgos"]:
            lines.append(f"- [{r['nivel']}] {r['area']}: {r['texto']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    args = parse_args()
    conn = connect(Path(args.db))
    resultados = [auditar_centro(conn, e, c, n, args.year, args.control_sano) for e, c, n in centros_desde_args(args)]
    guardar(conn, resultados)
    resumen = escribir_resumen(resultados)
    if args.json:
        print(json.dumps({"resultados": resultados, "resumen": str(resumen)}, ensure_ascii=False, indent=2))
    else:
        for item in resultados:
            d = item["datos"]
            p = d["presupuesto"]
            print(f"{d['centro']}: {item['estado']} score {item['score']}/100")
            print(f"  ingresos {p['ingresos']:.2f} gastos {p['gastos']:.2f} resultado {p['resultado']:.2f}")
            print(f"  reservas {d['reservas']} pedidos {d['pedidos']} produccion {d['partes_produccion']}/{d['lineas_produccion']} riesgos {len(item['riesgos'])}")
        print(f"Resumen: {resumen}")


if __name__ == "__main__":
    main()

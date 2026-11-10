# -*- coding: utf-8 -*-
"""Auditoria compacta de prueba de fuego para el simulador Hotel 1.

Cruza recetas, produccion, compras, stock y vida simulada. La salida debe ser
simple: que rompe, cuanto rompe y que hay que mirar primero.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auditar prueba de fuego del simulador Hotel 1.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--control-sano",
        action="store_true",
        help="Lectura base: supone operativa correcta y solo deja bloqueos reales o pocos datos pendientes.",
    )
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = (), default=0):
    if not all(table_exists(conn, table) for table in _tables_in_sql(sql)):
        return default
    row = conn.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else default


def _tables_in_sql(sql: str) -> set[str]:
    known = {
        "recetas", "recetas_ingredientes", "lineas_produccion", "partes_produccion",
        "simulador_escandallos_diarios_plato", "pedidos", "lineas_pedido",
        "recepciones", "productos", "simulador_vida_kpis_diarios",
        "simulador_produccion_resumen_diario", "menu_platos", "menu_subdepartamentos",
        "menus_diarios",
    }
    lowered = sql.lower()
    return {table for table in known if table in lowered}


def latest_corrida(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "simulador_vida_corridas"):
        return 0
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_prueba_fuego_detalle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            area TEXT NOT NULL,
            nivel TEXT NOT NULL,
            codigo TEXT NOT NULL,
            titulo TEXT NOT NULL,
            clase TEXT DEFAULT 'revision_operativa',
            valor REAL DEFAULT 0,
            referencia REAL DEFAULT 0,
            detalle TEXT,
            accion TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_prueba_fuego_resumen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            score REAL DEFAULT 0,
            criticas INTEGER DEFAULT 0,
            altas INTEGER DEFAULT 0,
            medias INTEGER DEFAULT 0,
            lectura TEXT,
            payload_json TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    add_column_if_missing(conn, "simulador_prueba_fuego_resumen", "criticas INTEGER DEFAULT 0")
    add_column_if_missing(conn, "simulador_prueba_fuego_resumen", "altas INTEGER DEFAULT 0")
    add_column_if_missing(conn, "simulador_prueba_fuego_resumen", "medias INTEGER DEFAULT 0")
    add_column_if_missing(conn, "simulador_prueba_fuego_resumen", "payload_json TEXT")
    add_column_if_missing(conn, "simulador_prueba_fuego_resumen", "empresa_id INTEGER DEFAULT 1")
    add_column_if_missing(conn, "simulador_prueba_fuego_detalle", "referencia REAL DEFAULT 0")
    add_column_if_missing(conn, "simulador_prueba_fuego_detalle", "accion TEXT")
    add_column_if_missing(conn, "simulador_prueba_fuego_detalle", "empresa_id INTEGER DEFAULT 1")
    add_column_if_missing(conn, "simulador_prueba_fuego_detalle", "clase TEXT DEFAULT 'revision_operativa'")


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_column_if_missing(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.strip().split()[0]
    if name not in columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def issue(area: str, nivel: str, codigo: str, titulo: str, valor=0, referencia=0, detalle="", accion="", clase="revision_operativa") -> dict:
    return {
        "area": area,
        "nivel": nivel,
        "codigo": codigo,
        "titulo": titulo,
        "clase": clase,
        "valor": round(float(valor or 0), 3),
        "referencia": round(float(referencia or 0), 3),
        "detalle": detalle,
        "accion": accion,
    }


def audit_recetas(conn: sqlite3.Connection, centro_id: int) -> list[dict]:
    issues: list[dict] = []
    total = scalar(conn, "SELECT COUNT(*) FROM recetas WHERE empresa_id=?", (EMPRESA_ID,), 0)
    sin_ing_catalogo = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM recetas r
        WHERE r.empresa_id=?
          AND (? IS NULL OR r.centro_id=? OR r.centro_id IS NULL)
          AND NOT EXISTS (SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id=r.id)
        """,
        (EMPRESA_ID, centro_id, centro_id),
        0,
    )
    sin_alg_catalogo = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM recetas r
        WHERE r.empresa_id=?
          AND (? IS NULL OR r.centro_id=? OR r.centro_id IS NULL)
          AND TRIM(COALESCE(r.alergenos,''))=''
        """,
        (EMPRESA_ID, centro_id, centro_id),
        0,
    )
    prod_sin_ing = scalar(
        conn,
        """
        SELECT COUNT(DISTINCT r.id)
        FROM recetas r
        JOIN lineas_produccion lp ON lp.receta_id=r.id
        JOIN partes_produccion pp ON pp.id=lp.parte_id
        WHERE r.empresa_id=? AND pp.centro_id=?
          AND NOT EXISTS (SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id=r.id)
        """,
        (EMPRESA_ID, centro_id),
        0,
    )
    menu_sin_alg = scalar(
        conn,
        """
        SELECT COUNT(DISTINCT r.id)
        FROM recetas r
        JOIN menu_platos mp ON mp.receta_id=r.id
        JOIN menu_subdepartamentos ms ON ms.id=mp.menu_subdepartamento_id
        JOIN menus_diarios md ON md.id=ms.menu_diario_id
        WHERE r.empresa_id=? AND md.centro_id=?
          AND TRIM(COALESCE(r.alergenos,''))=''
        """,
        (EMPRESA_ID, centro_id),
        0,
    )
    if prod_sin_ing:
        issues.append(issue("produccion", "critica", "produccion_receta_vacia", "Produccion con recetas sin ingredientes", prod_sin_ing, 0, "Hay partes produciendo platos sin escandallo.", "Bloquear paso a compras si la receta no tiene ingredientes.", "dato_incompleto"))
    elif sin_ing_catalogo:
        issues.append(issue("recetas", "media", "recetas_sin_ingredientes_catalogo", "Recetas de catalogo sin ingredientes", sin_ing_catalogo, total, "No bloquean la produccion actual, pero no permiten coste si entran en menu.", "Saneamiento progresivo por uso real y fuentes validadas.", "dato_incompleto_no_bloqueante"))
    if menu_sin_alg:
        issues.append(issue("menus", "alta", "menu_sin_alergenos", "Menu con recetas sin alergenos", menu_sin_alg, 0, "El menu puede imprimir etiquetas incompletas.", "Completar alergenos antes del servicio.", "bloqueo_legal_operativo"))
    elif sin_alg_catalogo:
        issues.append(issue("recetas", "media", "recetas_sin_alergenos_catalogo", "Recetas de catalogo sin alergenos", sin_alg_catalogo, total, "No bloquean el menu actual, pero no son legalmente fiables si se usan.", "Completar alergenos con proveedor, receta validada o fuente contrastada.", "dato_incompleto_no_bloqueante"))
    return issues


def audit_produccion(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> list[dict]:
    issues: list[dict] = []
    if table_exists(conn, "simulador_escandallos_diarios_plato"):
        total = scalar(conn, "SELECT COUNT(*) FROM simulador_escandallos_diarios_plato WHERE corrida_id=? AND centro_id=?", (corrida_id, centro_id), 0)
        altos = scalar(conn, "SELECT COUNT(*) FROM simulador_escandallos_diarios_plato WHERE corrida_id=? AND centro_id=? AND riesgo='alto'", (corrida_id, centro_id), 0)
        sin_teorico = scalar(conn, "SELECT COUNT(*) FROM simulador_escandallos_diarios_plato WHERE corrida_id=? AND centro_id=? AND COALESCE(coste_teorico,0)<=0 AND COALESCE(coste_real,0)>0", (corrida_id, centro_id), 0)
        merma = scalar(conn, "SELECT ROUND(SUM(COALESCE(coste_merma_estimado,0)),2) FROM simulador_escandallos_diarios_plato WHERE corrida_id=? AND centro_id=?", (corrida_id, centro_id), 0)
        merma_alta = scalar(conn, "SELECT ROUND(SUM(COALESCE(coste_merma_estimado,0)),2) FROM simulador_escandallos_diarios_plato WHERE corrida_id=? AND centro_id=? AND riesgo='alto'", (corrida_id, centro_id), 0)
        temp_revision = scalar(conn, "SELECT COUNT(*) FROM simulador_escandallos_diarios_plato WHERE corrida_id=? AND centro_id=? AND riesgo='alto' AND lectura LIKE '%temperatura%'", (corrida_id, centro_id), 0)
        incidencias = scalar(conn, "SELECT COUNT(*) FROM simulador_escandallos_diarios_plato WHERE corrida_id=? AND centro_id=? AND riesgo='alto' AND lectura LIKE '%incidencia%'", (corrida_id, centro_id), 0)
        coste_racion_alto = scalar(conn, "SELECT COUNT(*) FROM simulador_escandallos_diarios_plato WHERE corrida_id=? AND centro_id=? AND riesgo='alto' AND lectura LIKE '%coste por racion alto%'", (corrida_id, centro_id), 0)
        ratio_altos = (float(altos or 0) / float(total or 1)) if total else 0.0
        if altos:
            nivel_altos = "alta" if ratio_altos >= 0.10 else "media"
            detalle = (
                "No significa que la ERP no calcule: significa que cocina debe revisar coste, racionado, "
                "temperatura, incidencia o sobrante antes de repetir el menu."
            )
            issues.append(issue("produccion", nivel_altos, "escandallos_revision_operativa", "Escandallos con revision operativa", altos, total, detalle, "Priorizar platos repetidos con merma alta y coste por racion alto.", "revision_operativa"))
        if coste_racion_alto:
            issues.append(issue("produccion", "alta", "coste_racion_alto", "Platos con coste por racion alto", coste_racion_alto, total, "El plato puede ser correcto, pero necesita precio proveedor, gramaje y precio/valor de venta bajo control.", "Revisar ficha tecnica, proveedor alternativo y racionado.", "coste_real"))
        if temp_revision:
            issues.append(issue("appcc", "alta", "temperatura_fin_revision", "Escandallos con temperatura a revisar", temp_revision, total, "Riesgo APPCC operativo, no fallo de calculo.", "Revisar mantenimiento caliente/frio, tiempos de exposicion y registro APPCC.", "bloqueo_seguridad_si_confirma"))
        if incidencias:
            issues.append(issue("produccion", "media", "incidencias_produccion", "Incidencias de produccion en escandallos", incidencias, total, "Incidencias creadas por el simulador para probar respuesta del equipo.", "Clasificar motivo: falta producto, sobreproduccion, equipo, receta o error humano.", "simulacion_extrema"))
        if sin_teorico:
            issues.append(issue("produccion", "critica", "coste_teorico_cero", "Coste teorico inexistente", sin_teorico, total, "Hay coste real pero la receta no permite comparar.", "Completar ingredientes y precio antes de analizar margen.", "dato_incompleto_bloqueante"))
        if merma > 0:
            nivel = "alta" if merma_alta > 500 else "media"
            issues.append(issue("cocina", nivel, "merma_valorada", "Merma valorada de produccion", merma, merma_alta, "Coste estimado de sobrantes no reaprovechados. La referencia indica la parte asociada a escandallos de riesgo alto.", "Separar merma real, reaprovechable y guardado APPCC.", "coste_real"))
    pax_sin_prod = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM simulador_vida_kpis_diarios k
        LEFT JOIN simulador_produccion_resumen_diario p
          ON p.fecha=k.fecha AND p.corrida_id=k.corrida_id AND p.centro_id=k.centro_id
        WHERE k.corrida_id=? AND k.centro_id=? AND COALESCE(k.pax,0)>0 AND p.id IS NULL
        """,
        (corrida_id, centro_id),
        0,
    )
    if pax_sin_prod:
        issues.append(issue("calendario", "critica", "clientes_sin_produccion", "Dias con clientes sin produccion", pax_sin_prod, 0, "La vida hotelera tiene pax pero cocina no genera partes.", "Generar partes y necesidades desde ocupacion/menu.", "dato_incompleto_bloqueante"))
    return issues


def audit_compras_stock(conn: sqlite3.Connection, centro_id: int) -> list[dict]:
    issues: list[dict] = []
    pedidos_abiertos = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM pedidos
        WHERE empresa_id=?
          AND COALESCE(cerrado,0)=0
          AND COALESCE(cancelado,0)=0
          AND COALESCE(estado,'') NOT IN ('cancelado','cerrado','recibido','facturado')
        """,
        (EMPRESA_ID,),
        0,
    )
    borradores_sin_lineas = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM pedidos p
        WHERE p.empresa_id=? AND COALESCE(p.estado,'')='borrador'
          AND NOT EXISTS (SELECT 1 FROM lineas_pedido lp WHERE lp.pedido_id=p.id)
        """,
        (EMPRESA_ID,),
        0,
    )
    pedidos_sin_lineas = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM pedidos p
        WHERE p.empresa_id=? AND COALESCE(p.estado,'')<>'borrador'
          AND COALESCE(p.estado,'') NOT IN ('cancelado','rechazado')
          AND COALESCE(p.cancelado,0)=0
          AND NOT EXISTS (SELECT 1 FROM lineas_pedido lp WHERE lp.pedido_id=p.id)
        """,
        (EMPRESA_ID,),
        0,
    )
    pedidos_sin_recepcion = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM pedidos p
        WHERE p.empresa_id=? AND p.estado IN ('enviado','aprobado','pendiente')
          AND COALESCE(p.cancelado,0)=0
          AND NOT EXISTS (SELECT 1 FROM recepciones r WHERE r.pedido_id=p.id)
        """,
        (EMPRESA_ID,),
        0,
    )
    bajo_minimo = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM (
            SELECT p.id,
                   COALESCE(NULLIF(st.stock_total, 0), p.cantidad, 0) AS stock_total,
                   COALESCE(NULLIF(st.stock_minimo_almacen, 0), p.stock_minimo, 0) AS stock_minimo_real
            FROM productos p
            LEFT JOIN (
                SELECT producto_id,
                       SUM(COALESCE(cantidad, 0)) AS stock_total,
                       MAX(COALESCE(stock_minimo, 0)) AS stock_minimo_almacen
                FROM stock_almacen
                GROUP BY producto_id
            ) st ON st.producto_id=p.id
            WHERE p.empresa_id=? AND p.activo=1
              AND (? IS NULL OR p.centro_id=? OR p.centro_id IS NULL)
        ) s
        WHERE s.stock_minimo_real > 0
          AND s.stock_total < s.stock_minimo_real
        """,
        (EMPRESA_ID, centro_id, centro_id),
        0,
    )
    cancelados_flag_mal = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM pedidos
        WHERE empresa_id=? AND estado='cancelado' AND COALESCE(cancelado,0)=0
        """,
        (EMPRESA_ID,),
        0,
    )
    if pedidos_abiertos > 80:
        issues.append(issue("compras", "media", "muchos_pedidos_abiertos", "Muchos pedidos abiertos", pedidos_abiertos, 80, "Puede haber ruido operativo si no se cierran.", "Revisar recepciones, cierres e incidencias."))
    if pedidos_sin_lineas:
        issues.append(issue("compras", "critica", "pedidos_sin_lineas", "Pedidos sin lineas", pedidos_sin_lineas, 0, "Pedido no puede recepcionarse ni valorar stock.", "Bloquear creacion de pedido vacio."))
    if borradores_sin_lineas:
        issues.append(issue("compras", "media", "borradores_sin_lineas", "Borradores sin lineas", borradores_sin_lineas, 0, "Puede ser normal si el usuario esta preparando el pedido.", "Avisar en listado y limpiar borradores antiguos."))
    if cancelados_flag_mal:
        issues.append(issue("compras", "media", "cancelados_flag_incoherente", "Pedidos cancelados con flag incoherente", cancelados_flag_mal, 0, "El estado dice cancelado pero el flag tecnico no acompana.", "Normalizar estado/flag para que informes no cuenten pedidos muertos."))
    if pedidos_sin_recepcion:
        issues.append(issue("compras", "alta", "pedidos_sin_recepcion", "Pedidos enviados sin recepcion", pedidos_sin_recepcion, 0, "La mercancia puede estar pedida pero no entrada.", "Simular recepcion parcial, incidencias y cierre."))
    if bajo_minimo:
        issues.append(issue("stock", "alta", "productos_bajo_minimo", "Productos bajo minimo", bajo_minimo, 0, "Riesgo de rotura de servicio.", "Crear necesidades por produccion y proveedor principal."))
    return issues


def persist(conn: sqlite3.Connection, corrida_id: int, centro_id: int, issues: list[dict]) -> dict:
    conn.execute("DELETE FROM simulador_prueba_fuego_detalle WHERE corrida_id=? AND centro_id=?", (corrida_id, centro_id))
    conn.execute("DELETE FROM simulador_prueba_fuego_resumen WHERE corrida_id=? AND centro_id=?", (corrida_id, centro_id))
    for item in issues:
        conn.execute(
            """
            INSERT INTO simulador_prueba_fuego_detalle
            (corrida_id, centro_id, area, nivel, codigo, titulo, clase, valor, referencia, detalle, accion, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                centro_id,
                item["area"],
                item["nivel"],
                item["codigo"],
                item["titulo"],
                item.get("clase") or "revision_operativa",
                item["valor"],
                item["referencia"],
                item["detalle"],
                item["accion"],
                EMPRESA_ID,
            ),
        )
    criticas = sum(1 for i in issues if i["nivel"] == "critica")
    altas = sum(1 for i in issues if i["nivel"] == "alta")
    medias = sum(1 for i in issues if i["nivel"] == "media")
    penalizacion = 0.0
    for item in issues:
        clase = item.get("clase") or "revision_operativa"
        if item["nivel"] == "critica":
            penalizacion += 18 if "bloqueante" in clase or "bloqueo" in clase else 12
        elif item["nivel"] == "alta":
            penalizacion += 9 if "bloqueo" in clase else 6
        elif item["nivel"] == "media":
            penalizacion += 2 if clase == "simulacion_extrema" else 4
    score = max(0, 100 - penalizacion)
    if criticas:
        lectura = "No esta lista: hay roturas criticas que falsean datos o servicio."
    elif altas:
        lectura = "Va en camino, pero aun hay riesgos altos antes de confiar en decisiones."
    elif medias:
        lectura = "Base usable con mejoras operativas pendientes."
    else:
        lectura = "Prueba de fuego limpia para esta pasada."
    payload = {
        "corrida_id": corrida_id,
        "centro_id": centro_id,
        "score": score,
        "criticas": criticas,
        "altas": altas,
        "medias": medias,
        "lectura": lectura,
        "issues": issues,
    }
    conn.execute(
        """
        INSERT INTO simulador_prueba_fuego_resumen
        (corrida_id, centro_id, score, criticas, altas, medias, lectura, payload_json, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (corrida_id, centro_id, score, criticas, altas, medias, lectura, json.dumps(payload, ensure_ascii=False), EMPRESA_ID),
    )
    conn.commit()
    return payload


def modo_control_sano(issues: list[dict]) -> list[dict]:
    """Convierte la prueba bruta en una lectura de hotel sano.

    La simulacion extrema sigue guardada en sus tablas, pero esta vista contesta:
    si todo ficha, se compra a tiempo, APPCC/alergenos estan al dia y solo queda
    algun dato suelto, que debe ver direccion.
    """
    bloqueantes = [
        item for item in issues
        if item.get("clase") in {"dato_incompleto_bloqueante", "bloqueo_legal_operativo", "bloqueo_seguridad"}
        or item.get("nivel") == "critica"
    ]
    if bloqueantes:
        return bloqueantes[:2]

    candidatos = [
        item for item in issues
        if item.get("clase") in {"dato_incompleto_no_bloqueante", "coste_real", "bloqueo_seguridad_si_confirma"}
    ]
    salida = []
    for item in candidatos[:2]:
        limpio = dict(item)
        limpio["nivel"] = "media" if limpio["nivel"] in {"alta", "critica"} else limpio["nivel"]
        limpio["clase"] = "dato_o_control_pendiente"
        limpio["detalle"] = (
            "Modo control sano: la operativa general se considera correcta; queda como aviso "
            "para revisar, no como rotura de ERP."
        )
        salida.append(limpio)
    return salida


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    if not db.is_absolute():
        db = ROOT / db
    conn = connect(db)
    try:
        ensure_schema(conn)
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida de simulacion.")
        issues = []
        issues.extend(audit_recetas(conn, args.centro_id))
        issues.extend(audit_produccion(conn, corrida_id, args.centro_id))
        issues.extend(audit_compras_stock(conn, args.centro_id))
        if args.control_sano:
            issues = modo_control_sano(issues)
        payload = persist(conn, corrida_id, args.centro_id, issues)
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload)


if __name__ == "__main__":
    main()

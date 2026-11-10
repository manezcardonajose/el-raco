"""Genera plan semanal operativo y alertas de mise en place para Hotel 1.

Parte de los partes/lineas ya simulados y cruza recetas, ingredientes y productos.
El objetivo es avisar antes del servicio: sacar pescado/carne de camara,
descongelar, desinfectar verduras y revisar faltantes.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DEFAULT = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3

SERVICIO_HORA = {
    "desayuno": time(7, 30),
    "almuerzo": time(12, 30),
    "cena": time(18, 30),
    "snack": time(10, 0),
}


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def norm(value: str | None) -> str:
    return (value or "").strip().lower()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_planificacion_menu_semanal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            semana_inicio TEXT NOT NULL,
            fecha TEXT NOT NULL,
            servicio TEXT NOT NULL,
            partes INTEGER DEFAULT 0,
            lineas INTEGER DEFAULT 0,
            pax_previstos REAL DEFAULT 0,
            platos TEXT,
            kg_estimados REAL DEFAULT 0,
            kg_pescado REAL DEFAULT 0,
            kg_carne REAL DEFAULT 0,
            kg_congelado REAL DEFAULT 0,
            alertas INTEGER DEFAULT 0,
            lectura TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, fecha, servicio)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_alertas_mise_en_place (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            fecha_alerta TEXT NOT NULL,
            fecha_servicio TEXT NOT NULL,
            servicio TEXT NOT NULL,
            prioridad TEXT NOT NULL,
            tipo TEXT NOT NULL,
            producto_id INTEGER,
            producto TEXT NOT NULL,
            cantidad REAL DEFAULT 0,
            unidad TEXT,
            accion TEXT,
            estado TEXT DEFAULT 'pendiente',
            origen_tabla TEXT,
            origen_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, fecha_alerta, fecha_servicio, servicio, tipo, producto_id)
        )
        """
    )


def week_start(fecha: str) -> str:
    d = date.fromisoformat(fecha)
    return (d - timedelta(days=d.weekday())).isoformat()


def service_datetime(fecha: str, servicio: str) -> datetime:
    return datetime.combine(date.fromisoformat(fecha), SERVICIO_HORA.get(norm(servicio), time(8, 0)))


def stock_producto(conn: sqlite3.Connection, producto_id: int) -> float:
    if table_exists(conn, "lotes"):
        row = conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(cantidad_actual,0)),0)
            FROM lotes
            WHERE producto_id=? AND empresa_id=? AND COALESCE(estado,'activo') NOT IN ('consumido','baja','deteriorado')
            """,
            (producto_id, EMPRESA_ID),
        ).fetchone()
        return float(row[0] or 0)
    row = conn.execute("SELECT COALESCE(cantidad,0) FROM productos WHERE id=?", (producto_id,)).fetchone()
    return float(row[0] or 0) if row else 0.0


def horas_preparacion(producto: sqlite3.Row) -> tuple[str, int, str]:
    familia = norm(producto["familia"])
    nombre = norm(producto["nombre"])
    conservacion = norm(producto["tipo_conservacion"])
    unidad = norm(producto["unidad"])
    declaradas = int(producto["tiempo_descongelado_horas"] or 0)
    es_fruto_seco = any(t in familia for t in ["fruto seco", "frutos secos"]) or any(t in nombre for t in ["almendra", "nuez", "avellana", "pistacho", "cacahuete"])
    if "lata" in nombre or "conserva" in conservacion or unidad in {"lata", "bote", "unidad"}:
        return "preparar_conserva", 3, "Revisar stock, abrir/preparar conserva segun produccion y APPCC."
    if any(t in nombre for t in ["caldo", "fondo", "fumet", "crema ", "salsa", "knor", "knorr", "demi glace"]):
        if "congel" in conservacion:
            return "descongelar_base", max(declaradas, 12), "Sacar base/salsa congelada y registrar descongelacion controlada."
        return "preparar_base", 6, "Revisar stock y preparar base/salsa para el servicio."
    if "pesc" in familia or "marisc" in familia or any(t in nombre for t in ["merluza", "atun", "rodaballo", "langost", "salmon"]):
        return "sacar_pescado", max(declaradas, 24), "Sacar pescado/marisco a descongelacion controlada y registrar temperatura."
    if "carne" in familia or any(t in nombre for t in ["pollo", "cerdo", "ternera", "cordero"]):
        return "sacar_carne", max(declaradas, 24), "Sacar carne a descongelacion o preelaboracion controlada."
    if "congel" in conservacion:
        return "descongelar", max(declaradas, 24), "Sacar producto congelado segun ficha y ubicar en frio positivo."
    if int(producto["requiere_desinfeccion"] or 0) or "verd" in familia or ("frut" in familia and not es_fruto_seco):
        return "desinfectar", 3, "Preparar lavado/desinfeccion antes del servicio."
    return "", 0, ""


def generar(conn: sqlite3.Connection, corrida_id: int, desde: str, dias: int) -> dict:
    ensure_schema(conn)
    hasta = (date.fromisoformat(desde) + timedelta(days=dias - 1)).isoformat()
    conn.execute(
        "DELETE FROM simulador_planificacion_menu_semanal WHERE corrida_id=? AND centro_id=? AND fecha BETWEEN ? AND ?",
        (corrida_id, CENTRO_ID, desde, hasta),
    )
    conn.execute(
        "DELETE FROM simulador_alertas_mise_en_place WHERE corrida_id=? AND centro_id=? AND fecha_servicio BETWEEN ? AND ?",
        (corrida_id, CENTRO_ID, desde, hasta),
    )

    partes = conn.execute(
        """
        SELECT pp.id, pp.fecha, LOWER(COALESCE(pp.turno,'')) servicio,
               COALESCE(pp.pax_previstos,0) pax_previstos,
               COUNT(lp.id) lineas,
               GROUP_CONCAT(DISTINCT r.nombre) platos
        FROM partes_produccion pp
        JOIN lineas_produccion lp ON lp.parte_id=pp.id
        JOIN recetas r ON r.id=lp.receta_id
        WHERE pp.empresa_id=? AND pp.centro_id=? AND date(pp.fecha) BETWEEN date(?) AND date(?)
        GROUP BY pp.id
        ORDER BY pp.fecha, servicio
        """,
        (EMPRESA_ID, CENTRO_ID, desde, hasta),
    ).fetchall()

    resumen: dict[tuple[str, str], dict] = {}
    alertas = 0
    faltantes = 0
    for parte in partes:
        fecha = parte["fecha"]
        servicio = norm(parte["servicio"]) or "servicio"
        clave = (fecha, servicio)
        item = resumen.setdefault(
            clave,
            {
                "fecha": fecha,
                "servicio": servicio,
                "partes": 0,
                "lineas": 0,
                "pax": 0.0,
                "platos": set(),
                "kg": 0.0,
                "pescado": 0.0,
                "carne": 0.0,
                "congelado": 0.0,
                "alertas": 0,
            },
        )
        item["partes"] += 1
        item["lineas"] += int(parte["lineas"] or 0)
        item["pax"] += float(parte["pax_previstos"] or 0)
        for plato in (parte["platos"] or "").split(","):
            plato = plato.strip()
            if plato:
                item["platos"].add(plato)

        ingredientes = conn.execute(
            """
            SELECT p.id producto_id, p.nombre, p.unidad, p.familia, p.tipo_conservacion,
                   p.tiempo_descongelado_horas, p.requiere_desinfeccion,
                   SUM(COALESCE(pir.cantidad_planificada, pir.cantidad_real, 0)) cantidad
            FROM lineas_produccion lp
            JOIN produccion_ingredientes_reales pir ON pir.linea_produccion_id=lp.id
            JOIN productos p ON p.id=pir.producto_id
            WHERE lp.parte_id=?
            GROUP BY p.id
            """,
            (parte["id"],),
        ).fetchall()
        for prod in ingredientes:
            cantidad = float(prod["cantidad"] or 0)
            familia = norm(prod["familia"])
            conservacion = norm(prod["tipo_conservacion"])
            item["kg"] += cantidad
            if "pesc" in familia or "marisc" in familia:
                item["pescado"] += cantidad
            if "carne" in familia or any(t in norm(prod["nombre"]) for t in ["pollo", "cerdo", "ternera"]):
                item["carne"] += cantidad
            if "congel" in conservacion:
                item["congelado"] += cantidad

            tipo, horas, accion_base = horas_preparacion(prod)
            if not tipo:
                continue
            fecha_alerta = service_datetime(fecha, servicio) - timedelta(hours=horas)
            inicio_simulacion = datetime.combine(date.fromisoformat(desde), time(6, 0))
            aviso_vencido = fecha_alerta < inicio_simulacion
            if aviso_vencido:
                fecha_alerta = inicio_simulacion
            stock = stock_producto(conn, int(prod["producto_id"]))
            prioridad = "alta" if tipo in {"sacar_pescado", "sacar_carne", "descongelar", "descongelar_base"} else "media"
            accion = accion_base
            if aviso_vencido:
                prioridad = "critica"
                accion = f"URGENTE: {accion_base}"
            if stock < cantidad:
                prioridad = "critica"
                faltantes += 1
                accion = f"Falta stock: pedir o buscar alternativa. Necesario {cantidad:.2f} {prod['unidad'] or ''}, stock {stock:.2f}."
            conn.execute(
                """
                INSERT INTO simulador_alertas_mise_en_place
                (corrida_id, fecha_alerta, fecha_servicio, servicio, prioridad, tipo,
                 producto_id, producto, cantidad, unidad, accion, origen_tabla, origen_id,
                 empresa_id, centro_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'partes_produccion', ?, ?, ?)
                ON CONFLICT(corrida_id, centro_id, fecha_alerta, fecha_servicio, servicio, tipo, producto_id)
                DO UPDATE SET
                    prioridad=excluded.prioridad,
                    cantidad=excluded.cantidad,
                    accion=excluded.accion,
                    estado='pendiente'
                """,
                (
                    corrida_id,
                    fecha_alerta.strftime("%Y-%m-%d %H:%M"),
                    fecha,
                    servicio,
                    prioridad,
                    tipo,
                    prod["producto_id"],
                    prod["nombre"],
                    round(cantidad, 3),
                    prod["unidad"] or "",
                    accion,
                    parte["id"],
                    EMPRESA_ID,
                    CENTRO_ID,
                ),
            )
            item["alertas"] += 1
            alertas += 1

    for item in resumen.values():
        platos = sorted(item["platos"])
        lectura = f"{item['partes']} partes, {item['lineas']} lineas, {item['pax']:.0f} pax previstos."
        if item["pescado"] > 0:
            lectura += f" Sacar pescado/marisco: {item['pescado']:.2f} kg."
        if item["carne"] > 0:
            lectura += f" Sacar/preparar carne: {item['carne']:.2f} kg."
        if item["alertas"] == 0:
            lectura += " Sin alertas previas."
        conn.execute(
            """
            INSERT INTO simulador_planificacion_menu_semanal
            (corrida_id, semana_inicio, fecha, servicio, partes, lineas, pax_previstos,
             platos, kg_estimados, kg_pescado, kg_carne, kg_congelado, alertas,
             lectura, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(corrida_id, centro_id, fecha, servicio)
            DO UPDATE SET
                semana_inicio=excluded.semana_inicio,
                partes=excluded.partes,
                lineas=excluded.lineas,
                pax_previstos=excluded.pax_previstos,
                platos=excluded.platos,
                kg_estimados=excluded.kg_estimados,
                kg_pescado=excluded.kg_pescado,
                kg_carne=excluded.kg_carne,
                kg_congelado=excluded.kg_congelado,
                alertas=excluded.alertas,
                lectura=excluded.lectura
            """,
            (
                corrida_id,
                week_start(item["fecha"]),
                item["fecha"],
                item["servicio"],
                item["partes"],
                item["lineas"],
                round(item["pax"], 2),
                json.dumps(platos[:20], ensure_ascii=False),
                round(item["kg"], 3),
                round(item["pescado"], 3),
                round(item["carne"], 3),
                round(item["congelado"], 3),
                item["alertas"],
                lectura,
                EMPRESA_ID,
                CENTRO_ID,
            ),
        )
    conn.commit()
    return {
        "desde": desde,
        "hasta": hasta,
        "servicios_planificados": len(resumen),
        "alertas_mise_en_place": alertas,
        "faltantes_criticos": faltantes,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--corrida-id", type=int, default=1)
    parser.add_argument("--desde", default="2026-05-30")
    parser.add_argument("--dias", type=int, default=14)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        result = generar(conn, args.corrida_id, args.desde, args.dias)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Genera operativa simulada de cocina/compras para Albarracin.

Usa los menus ya importados y reservas 2027. No valida costes reales:
las fichas de Albarracin son en parte cualitativas/alergenos, asi que las
lineas quedan marcadas como simulacion pendiente de escandallo.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
EMPRESA_ID = 2
CENTRO_ID = 8
YEAR = 2027
ORIGEN = "simulador_albarracin_prueba_fuego"

DIAS = {
    0: "LUNES",
    1: "MARTES",
    2: "MIERCOLES",
    3: "JUEVES",
    4: "VIERNES",
    5: "SABADO",
    6: "DOMINGO",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generar prueba fuego operativa Albarracin.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--year", type=int, default=YEAR)
    parser.add_argument("--reset", action="store_true")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def scalar(conn: sqlite3.Connection, sql: str, params=(), default=0):
    row = conn.execute(sql, params).fetchone()
    if not row:
        return default
    return row[0] if row[0] is not None else default


def reset(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> None:
    parte_ids = [
        row["id"]
        for row in conn.execute(
            """
            SELECT id FROM partes_produccion
            WHERE empresa_id=? AND centro_id=? AND COALESCE(observaciones,'') LIKE ?
            """,
            (empresa_id, centro_id, f"%{ORIGEN}%"),
        ).fetchall()
    ] if table_exists(conn, "partes_produccion") else []
    if parte_ids:
        marks = ",".join("?" for _ in parte_ids)
        conn.execute(f"DELETE FROM lineas_produccion WHERE parte_id IN ({marks})", tuple(parte_ids))
        conn.execute(f"DELETE FROM partes_produccion WHERE id IN ({marks})", tuple(parte_ids))

    pedido_ids = [
        row["id"]
        for row in conn.execute(
            """
            SELECT id FROM pedidos
            WHERE empresa_id=? AND COALESCE(origen_documento,'')=?
            """,
            (empresa_id, ORIGEN),
        ).fetchall()
    ] if table_exists(conn, "pedidos") else []
    if pedido_ids:
        marks = ",".join("?" for _ in pedido_ids)
        if table_exists(conn, "lineas_pedido"):
            conn.execute(f"DELETE FROM lineas_pedido WHERE pedido_id IN ({marks})", tuple(pedido_ids))
        recepcion_ids = [
            row["id"]
            for row in conn.execute(f"SELECT id FROM recepciones WHERE pedido_id IN ({marks})", tuple(pedido_ids)).fetchall()
        ] if table_exists(conn, "recepciones") else []
        if recepcion_ids and table_exists(conn, "lineas_recepcion"):
            rmarks = ",".join("?" for _ in recepcion_ids)
            conn.execute(f"DELETE FROM lineas_recepcion WHERE recepcion_id IN ({rmarks})", tuple(recepcion_ids))
        if recepcion_ids:
            rmarks = ",".join("?" for _ in recepcion_ids)
            conn.execute(f"DELETE FROM recepciones WHERE id IN ({rmarks})", tuple(recepcion_ids))
        conn.execute(f"DELETE FROM pedidos WHERE id IN ({marks})", tuple(pedido_ids))


def pax_servicio(conn: sqlite3.Connection, empresa_id: int, centro_id: int, fecha: str) -> int:
    if table_exists(conn, "recepcion_reservas_restaurante"):
        centro_sql = ""
        params = [empresa_id, fecha]
        if table_exists(conn, "puntos_venta"):
            centro_sql = "AND punto_venta_id IN (SELECT id FROM puntos_venta WHERE empresa_id=? AND centro_id=?)"
            params.extend([empresa_id, centro_id])
        total = scalar(
            conn,
            f"""
            SELECT COALESCE(SUM(personas),0)
            FROM recepcion_reservas_restaurante
            WHERE empresa_id=? AND fecha=?
              {centro_sql}
            """,
            tuple(params),
            0,
        )
        if total:
            return int(total)
    return int(scalar(
        conn,
        """
        SELECT COALESCE(SUM(adultos + menores),0)
        FROM reservas_recepcion
        WHERE empresa_id=? AND centro_id=?
          AND date(fecha_entrada) <= date(?)
          AND date(fecha_salida) > date(?)
          AND COALESCE(estado,'') NOT IN ('cancelada','cancelado','no_show','no show')
        """,
        (empresa_id, centro_id, fecha, fecha),
        0,
    ) or 0)


def recetas_dia(conn: sqlite3.Connection, empresa_id: int, centro_id: int, fecha: str, servicio: str) -> list[sqlite3.Row]:
    dia = DIAS[date.fromisoformat(fecha).weekday()]
    rows = conn.execute(
        """
        SELECT id, nombre, categoria
        FROM recetas
        WHERE empresa_id=? AND centro_id=?
          AND UPPER(COALESCE(categoria,''))=?
        ORDER BY id
        """,
        (empresa_id, centro_id, dia),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            """
            SELECT id, nombre, categoria
            FROM recetas
            WHERE empresa_id=? AND centro_id=?
            ORDER BY id
            LIMIT 8
            """,
            (empresa_id, centro_id),
        ).fetchall()
    if "cena" in servicio:
        return rows[2:6] or rows[:4]
    if "desayuno" in servicio:
        return rows[:3]
    return rows[:4]


def generar_produccion(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int) -> tuple[int, int]:
    menus = conn.execute(
        """
        SELECT *
        FROM menus_diarios
        WHERE empresa_id=? AND centro_id=? AND fecha LIKE ?
        ORDER BY fecha, id
        """,
        (empresa_id, centro_id, f"{year}-%"),
    ).fetchall()
    partes = 0
    lineas = 0
    for menu in menus:
        fecha = menu["fecha"]
        servicio = (menu["modo_servicio"] or "servicio").lower()
        turno = "cena" if "cena" in servicio else "desayuno" if "desayuno" in servicio else "almuerzo"
        pax = max(pax_servicio(conn, empresa_id, centro_id, fecha), 20)
        existente = scalar(
            conn,
            """
            SELECT COUNT(*) FROM partes_produccion
            WHERE empresa_id=? AND centro_id=? AND menu_diario_id=? AND turno=? AND COALESCE(observaciones,'') LIKE ?
            """,
            (empresa_id, centro_id, menu["id"], turno, f"%{ORIGEN}%"),
            0,
        )
        if existente:
            continue
        cur = conn.execute(
            """
            INSERT INTO partes_produccion
            (fecha, responsable, punto_venta_id, observaciones, estado, menu_diario_id,
             turno, fecha_prevista, notificado, empresa_id, pax_previstos, regla_calculo, centro_id)
            VALUES (?, 'Simulador Albarracin', ?, ?, 'planificado', ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                fecha,
                menu["punto_venta_id"],
                f"{ORIGEN}. Parte generado desde menu importado; coste no validado.",
                menu["id"],
                turno,
                fecha,
                empresa_id,
                pax,
                "pax_reservas_restaurante_o_ocupacion",
                centro_id,
            ),
        )
        parte_id = cur.lastrowid
        partes += 1
        for receta in recetas_dia(conn, empresa_id, centro_id, fecha, turno):
            raciones = round(pax * (0.40 if turno != "desayuno" else 0.25), 2)
            conn.execute(
                """
                INSERT INTO lineas_produccion
                (parte_id, receta_id, cantidad_planificada, cantidad_producida, cantidad_real,
                 estado, observaciones, coste_unitario_teorico, empresa_id, raciones_planificadas,
                 pax_previstos, estacion_buffet, porcentaje_prevision, regla_calculo)
                VALUES (?, ?, ?, 0, 0, 'pendiente_coste', ?, 0, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parte_id,
                    receta["id"],
                    raciones,
                    f"{ORIGEN}. Escandallo pendiente: receta importada sin cantidades fiables.",
                    empresa_id,
                    raciones,
                    pax,
                    "frio" if turno == "desayuno" else "caliente",
                    40 if turno != "desayuno" else 25,
                    "raciones_por_pax_simuladas",
                ),
            )
            lineas += 1
    return partes, lineas


def productos_por_proveedor(conn: sqlite3.Connection, empresa_id: int) -> dict[int, list[sqlite3.Row]]:
    rows = conn.execute(
        """
        SELECT id, nombre, precio, proveedor_id, familia
        FROM productos
        WHERE empresa_id=? AND COALESCE(activo,1)=1 AND proveedor_id IS NOT NULL
        ORDER BY proveedor_id, familia, id
        """,
        (empresa_id,),
    ).fetchall()
    data: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        data.setdefault(int(row["proveedor_id"]), []).append(row)
    return data


def generar_pedidos(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int) -> tuple[int, int]:
    cocina_id = int(scalar(conn, "SELECT id FROM departamentos WHERE empresa_id=? AND centro_id=? AND UPPER(nombre)='COCINA'", (empresa_id, centro_id), 0) or 0)
    almacen_id = int(scalar(conn, "SELECT id FROM almacenes WHERE empresa_id=? AND centro_id=? AND departamento_id=? ORDER BY id LIMIT 1", (empresa_id, centro_id, cocina_id), 0) or 0)
    proveedores = productos_por_proveedor(conn, empresa_id)
    pedidos = 0
    recepciones = 0
    fechas = [date(year, 2, 15) + timedelta(days=7 * i) for i in range(4)]
    for idx, fecha in enumerate(fechas, start=1):
        for proveedor_id, productos in list(proveedores.items())[:6]:
            numero = f"ALB-PF-{fecha.strftime('%Y%m%d')}-{proveedor_id}"
            if scalar(conn, "SELECT COUNT(*) FROM pedidos WHERE empresa_id=? AND numero_pedido=?", (empresa_id, numero), 0):
                continue
            entrega = fecha + timedelta(days=1)
            cur = conn.execute(
                """
                INSERT INTO pedidos
                (numero_pedido, proveedor_id, fecha_pedido, fecha_entrega, estado, observaciones,
                 creado_por, empresa_id, fecha_envio, departamento_solicitante_id,
                 tipo_pedido, origen_documento, validado_economicamente, departamento_id, solicitado_por)
                VALUES (?, ?, ?, ?, 'recibido', ?, 'Simulador Albarracin', ?, ?, ?, 'operativo', ?, 0, ?, 'Simulador Albarracin')
                """,
                (
                    numero,
                    proveedor_id,
                    fecha.isoformat(),
                    entrega.isoformat(),
                    f"{ORIGEN}. Pedido ficticio para probar compras, recepcion y coste.",
                    empresa_id,
                    fecha.isoformat(),
                    cocina_id or None,
                    ORIGEN,
                    cocina_id or None,
                ),
            )
            pedido_id = cur.lastrowid
            pedidos += 1
            total = 0.0
            for p in productos[:5]:
                cantidad = 8 + idx * 2
                precio = float(p["precio"] or 1.0)
                total += cantidad * precio
                conn.execute(
                    """
                    INSERT INTO lineas_pedido
                    (pedido_id, producto_id, cantidad_solicitada, cantidad_recibida, precio_unitario)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (pedido_id, p["id"], cantidad, cantidad, precio),
                )
            rec = conn.execute(
                """
                INSERT INTO recepciones
                (pedido_id, proveedor_id, fecha_recepcion, numero_albaran, observaciones, recibido_por,
                 empresa_id, tipo_recepcion, departamento_receptor_id, validada_operativamente,
                 validada_economicamente, stock_aplicado, departamento_id, estado_facturacion,
                 fecha_vencimiento_economica)
                VALUES (?, ?, ?, ?, ?, 'Simulador Albarracin', ?, 'pedido', ?, 1, 0, 1, ?, 'pendiente_factura', ?)
                """,
                (
                    pedido_id,
                    proveedor_id,
                    entrega.isoformat(),
                    f"ALB-SIM-{pedido_id}",
                    f"{ORIGEN}. Recepcion ficticia para prueba de fuego.",
                    empresa_id,
                    cocina_id or None,
                    cocina_id or None,
                    (entrega + timedelta(days=30)).isoformat(),
                ),
            )
            recepcion_id = rec.lastrowid
            recepciones += 1
            for p in productos[:5]:
                cantidad = 8 + idx * 2
                precio = float(p["precio"] or 1.0)
                conn.execute(
                    """
                    INSERT INTO lineas_recepcion
                    (recepcion_id, producto_id, cantidad_recibida, lote, fecha_caducidad,
                     temperatura, almacen_id, precio_unitario, importe_total, aceptado, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        recepcion_id,
                        p["id"],
                        cantidad,
                        f"PF-{pedido_id}-{p['id']}",
                        (entrega + timedelta(days=45)).isoformat(),
                        4.0,
                        almacen_id or None,
                        precio,
                        round(cantidad * precio, 2),
                        empresa_id,
                    ),
                )
    return pedidos, recepciones


def main() -> None:
    args = parse_args()
    conn = connect(Path(args.db))
    if args.reset:
        reset(conn, args.empresa_id, args.centro_id)
    partes, lineas = generar_produccion(conn, args.empresa_id, args.centro_id, args.year)
    pedidos, recepciones = generar_pedidos(conn, args.empresa_id, args.centro_id, args.year)
    conn.commit()
    print({
        "partes_produccion": partes,
        "lineas_produccion": lineas,
        "pedidos": pedidos,
        "recepciones": recepciones,
        "origen": ORIGEN,
        "fecha": datetime.now().isoformat(timespec="seconds"),
    })


if __name__ == "__main__":
    main()

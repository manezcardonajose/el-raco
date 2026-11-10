"""Simula recepciones de pedidos MISE-H1.

Crea recepciones reales de la ERP contra pedidos generados por mise en place.
Incluye entregas correctas, parciales, rechazos por temperatura y faltas.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DEFAULT = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--corrida-id", type=int, default=1)
    parser.add_argument("--prefijo", default="MISE-H1-")
    parser.add_argument("--seed", type=int, default=20260530)
    parser.add_argument("--max-pedidos", type=int, default=0)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def log_event(conn: sqlite3.Connection, corrida_id: int, fecha: str, tipo: str, severidad: str, descripcion: str, tabla_ref: str, registro_id: int | None, impacto: dict) -> None:
    if not table_exists(conn, "simulador_vida_eventos"):
        return
    conn.execute(
        """
        INSERT INTO simulador_vida_eventos
        (corrida_id, fecha_simulada, modulo, tipo, severidad, descripcion,
         tabla_ref, registro_id, impacto_json, empresa_id, centro_id)
        VALUES (?, ?, 'compras', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (corrida_id, fecha, tipo, severidad, descripcion, tabla_ref, registro_id, json.dumps(impacto, ensure_ascii=False), EMPRESA_ID, CENTRO_ID),
    )


def import_stock_helper(db_path: Path):
    os.environ["ERP_DATABASE"] = str(db_path)
    os.environ["PYTHONPATH"] = str(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from blueprints.compras.routes import _aplicar_stock_recepcion

    return _aplicar_stock_recepcion


def almacen_producto(conn: sqlite3.Connection, producto_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT COALESCE(p.almacen_preferente_id, sa.almacen_id) almacen_id
        FROM productos p
        LEFT JOIN stock_almacen sa ON sa.producto_id=p.id
        WHERE p.id=?
        ORDER BY sa.cantidad DESC
        LIMIT 1
        """,
        (producto_id,),
    ).fetchone()
    if row and row["almacen_id"]:
        return int(row["almacen_id"])
    row = conn.execute(
        """
        SELECT id FROM almacenes
        WHERE empresa_id=? AND COALESCE(activo,1)=1 AND (centro_id=? OR centro_id IS NULL)
        ORDER BY CASE WHEN centro_id=? THEN 0 ELSE 1 END, id
        LIMIT 1
        """,
        (EMPRESA_ID, CENTRO_ID, CENTRO_ID),
    ).fetchone()
    return int(row["id"]) if row else None


def temperatura_recepcion(producto: sqlite3.Row, forzar_mala: bool) -> float:
    tipo = (producto["tipo_conservacion"] or "").lower()
    fam = (producto["familia"] or "").lower()
    if "congel" in tipo:
        return round(random.uniform(-18, -14), 1) if not forzar_mala else round(random.uniform(-8, -3), 1)
    if "refriger" in tipo or "pesc" in fam or "carne" in fam or "lact" in fam:
        return round(random.uniform(0.5, 4.0), 1) if not forzar_mala else round(random.uniform(7, 11), 1)
    return round(random.uniform(12, 20), 1)


def incidencia(conn: sqlite3.Connection, recepcion_id: int, descripcion: str, gravedad: str, departamento_id: int | None) -> int:
    conn.execute(
        """
        INSERT INTO incidencias
        (tipo, referencia_id, fecha, usuario, descripcion, gravedad, estado,
         empresa_id, origen_modulo, destino_modulo, categoria_operativa,
         departamento_id, requiere_appcc, origen_tabla, origen_id)
        VALUES ('recepcion', ?, CURRENT_TIMESTAMP, 'Simulador recepcion MISE', ?, ?, 'abierta',
                ?, 'compras', 'compras', 'recepcion_compra', ?, 1, 'recepciones', ?)
        """,
        (recepcion_id, descripcion, gravedad, EMPRESA_ID, departamento_id, recepcion_id),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def recepcionar(conn: sqlite3.Connection, db_path: Path, args: argparse.Namespace) -> dict:
    aplicar_stock = import_stock_helper(db_path)
    pedidos = conn.execute(
        """
        SELECT p.*
        FROM pedidos p
        WHERE p.empresa_id=?
          AND p.numero_pedido LIKE ?
          AND NOT EXISTS (SELECT 1 FROM recepciones r WHERE r.pedido_id=p.id AND r.empresa_id=p.empresa_id)
        ORDER BY p.numero_pedido
        """,
        (EMPRESA_ID, f"{args.prefijo}%"),
    ).fetchall()
    if args.max_pedidos and args.max_pedidos > 0:
        pedidos = pedidos[: args.max_pedidos]

    creadas = 0
    lineas_creadas = 0
    incidencias = 0
    stock_aplicado = 0
    parciales = 0
    rechazadas = 0

    for pedido in pedidos:
        lineas = conn.execute(
            """
            SELECT lp.*, p.nombre, p.unidad, p.tipo_conservacion, p.familia, p.control_caducidad
            FROM lineas_pedido lp
            JOIN productos p ON p.id=lp.producto_id
            WHERE lp.pedido_id=?
            ORDER BY lp.id
            """,
            (pedido["id"],),
        ).fetchall()
        if not lineas:
            continue

        entrega = pedido["fecha_entrega"] or (date.fromisoformat(pedido["fecha_pedido"]) + timedelta(days=1)).isoformat()
        fallo_pedido = random.random() < 0.42
        numero_albaran = f"ALB-{pedido['numero_pedido']}"
        obs = "Recepcion simulada correcta desde mise en place."
        if fallo_pedido:
            obs = "Recepcion simulada con faltas/temperaturas para prueba operativa."
        conn.execute(
            """
            INSERT INTO recepciones
            (pedido_id, proveedor_id, fecha_recepcion, numero_albaran, observaciones,
             recibido_por, empresa_id, tipo_recepcion, departamento_receptor_id,
             validada_operativamente, validada_economicamente, stock_aplicado,
             departamento_id, estado_facturacion)
            VALUES (?, ?, ?, ?, ?, 'Simulador recepcion MISE', ?, 'pedido', ?, 1, 0, 0, ?, 'pendiente_factura')
            """,
            (
                pedido["id"],
                pedido["proveedor_id"],
                entrega,
                numero_albaran,
                obs,
                EMPRESA_ID,
                pedido["departamento_id"],
                pedido["departamento_id"],
            ),
        )
        recepcion_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        creadas += 1
        pedido_parcial = False

        for linea in lineas:
            solicitada = float(linea["cantidad_solicitada"] or 0)
            if solicitada <= 0:
                continue
            modo = random.choices(["ok", "parcial", "faltante", "temperatura"], weights=[62, 18, 8, 12], k=1)[0] if fallo_pedido else "ok"
            cantidad = solicitada
            aceptado = 1
            motivo = None
            inc_id = None
            if modo == "parcial":
                cantidad = round(solicitada * random.uniform(0.45, 0.82), 3)
                motivo = f"Entrega parcial: faltan {solicitada - cantidad:.3f} {linea['unidad'] or ''}."
                inc_id = incidencia(conn, recepcion_id, motivo, "media", pedido["departamento_id"])
                incidencias += 1
                pedido_parcial = True
            elif modo == "faltante":
                cantidad = 0.0
                aceptado = 0
                motivo = f"Proveedor no entrega {linea['nombre']}."
                inc_id = incidencia(conn, recepcion_id, motivo, "alta", pedido["departamento_id"])
                incidencias += 1
                pedido_parcial = True
                rechazadas += 1
            elif modo == "temperatura":
                aceptado = 0
                motivo = f"Rechazado por temperatura fuera de rango: {linea['nombre']}."
                inc_id = incidencia(conn, recepcion_id, motivo, "alta", pedido["departamento_id"])
                incidencias += 1
                pedido_parcial = True
                rechazadas += 1

            almacen_id = almacen_producto(conn, int(linea["producto_id"]))
            temp = temperatura_recepcion(linea, modo == "temperatura")
            cad = (date.fromisoformat(entrega) + timedelta(days=random.randint(4, 60))).isoformat()
            lote = f"MISE-{pedido['id']}-{linea['producto_id']}-{linea['id']}"
            conn.execute(
                """
                INSERT INTO lineas_recepcion
                (recepcion_id, producto_id, cantidad_recibida, lote, fecha_caducidad,
                 temperatura, almacen_id, precio_unitario, importe_total, aceptado,
                 motivo_rechazo, incidencia_id, empresa_id, num_envases)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recepcion_id,
                    linea["producto_id"],
                    cantidad,
                    lote,
                    cad,
                    temp,
                    almacen_id,
                    linea["precio_unitario"],
                    round(cantidad * float(linea["precio_unitario"] or 0), 2),
                    aceptado,
                    motivo,
                    inc_id,
                    EMPRESA_ID,
                    max(1, int(round(cantidad / 10))) if cantidad else 0,
                ),
            )
            conn.execute(
                """
                UPDATE lineas_pedido
                SET cantidad_recibida=COALESCE(cantidad_recibida,0)+?
                WHERE id=?
                """,
                (cantidad if aceptado else 0, linea["id"]),
            )
            lineas_creadas += 1

        aplicadas = aplicar_stock(conn.cursor(), recepcion_id, EMPRESA_ID, "Simulador recepcion MISE")
        stock_aplicado += int(aplicadas or 0)
        nuevo_estado = "recibido_parcial" if pedido_parcial else "recibido"
        estado_recepcion = "parcial" if pedido_parcial else "recibido"
        if pedido_parcial:
            parciales += 1
        conn.execute(
            """
            UPDATE pedidos
            SET estado=?, estado_recepcion=?, fecha_recepcion=?, fecha_envio=COALESCE(fecha_envio, ?),
                validado_economicamente=0, cerrado=0
            WHERE id=?
            """,
            (nuevo_estado, estado_recepcion, entrega, f"{pedido['fecha_pedido']} 10:00:00", pedido["id"]),
        )
        log_event(
            conn,
            args.corrida_id,
            entrega,
            "recepcion_mise",
            "alta" if pedido_parcial else "info",
            f"Recepcion MISE {pedido['numero_pedido']}: {estado_recepcion}, {lineas_creadas} lineas acumuladas.",
            "recepciones",
            recepcion_id,
            {"pedido_id": pedido["id"], "parcial": pedido_parcial, "stock_aplicado": aplicadas},
        )

    conn.commit()
    return {
        "recepciones_creadas": creadas,
        "lineas_recepcion": lineas_creadas,
        "incidencias": incidencias,
        "pedidos_parciales": parciales,
        "lineas_rechazadas": rechazadas,
        "stock_aplicado_lineas": stock_aplicado,
    }


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        result = recepcionar(conn, db_path, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

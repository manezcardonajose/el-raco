# -*- coding: utf-8 -*-
"""Resuelve pedidos recibidos parcialmente en la simulacion.

Cierra el pedido original con incidencia trazada y, si falta material con valor,
crea un pedido de rescate para probar la continuidad operativa.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolver pedidos parciales del simulador.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--min-importe-rescate", type=float, default=8.0)
    parser.add_argument("--max-pedidos", type=int, default=120)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_resolucion_pedidos_parciales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            pedido_original_id INTEGER NOT NULL,
            pedido_rescate_id INTEGER,
            fecha TEXT,
            proveedor_id INTEGER,
            lineas_pendientes INTEGER DEFAULT 0,
            importe_pendiente REAL DEFAULT 0,
            decision TEXT,
            incidencia_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, pedido_original_id)
        )
        """
    )


def log_event(conn: sqlite3.Connection, args: argparse.Namespace, pedido_id: int, fecha: str, decision: str, impacto: dict) -> None:
    if not table_exists(conn, "simulador_vida_eventos"):
        return
    conn.execute(
        """
        INSERT INTO simulador_vida_eventos
        (corrida_id, fecha_simulada, modulo, tipo, severidad, descripcion,
         tabla_ref, registro_id, impacto_json, empresa_id, centro_id)
        VALUES (?, ?, 'compras', 'resolver_pedido_parcial', 'media', ?,
                'pedidos', ?, ?, ?, ?)
        """,
        (
            args.corrida_id,
            fecha,
            f"Pedido parcial resuelto: {decision}.",
            pedido_id,
            json.dumps(impacto, ensure_ascii=False),
            EMPRESA_ID,
            args.centro_id,
        ),
    )


def crear_incidencia(conn: sqlite3.Connection, pedido: sqlite3.Row, importe: float, lineas: int) -> int | None:
    if not table_exists(conn, "incidencias"):
        return None
    cur = conn.execute(
        """
        INSERT INTO incidencias
        (tipo, referencia_id, fecha, usuario, descripcion, gravedad, estado,
         solucion, fecha_resolucion, resuelto_por, observaciones, empresa_id,
         origen_modulo, destino_modulo, categoria_operativa, departamento_id,
         origen_tabla, origen_id)
        VALUES ('proveedor', ?, CURRENT_TIMESTAMP, 'Simulador compras',
                ?, 'media', 'cerrada', ?, CURRENT_TIMESTAMP, 'Simulador compras',
                ?, ?, 'compras', 'compras', 'pedido_parcial', ?, 'pedidos', ?)
        """,
        (
            pedido["id"],
            f"Pedido {pedido['numero_pedido']} recibido parcialmente: {lineas} lineas pendientes por {importe:.2f} EUR.",
            "Se cierra el pedido original y se genera rescate si la falta afecta a operativa.",
            "Incidencia simulada para medir proveedor, compras y continuidad de stock.",
            EMPRESA_ID,
            pedido["departamento_id"],
            pedido["id"],
        ),
    )
    return int(cur.lastrowid)


def pendientes(conn: sqlite3.Connection, pedido_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT lp.*, p.nombre, COALESCE(p.proveedor_id, 0) proveedor_producto
        FROM lineas_pedido lp
        JOIN productos p ON p.id=lp.producto_id
        WHERE lp.pedido_id=?
          AND COALESCE(lp.cantidad_solicitada,0) > COALESCE(lp.cantidad_recibida,0)
        ORDER BY lp.id
        """,
        (pedido_id,),
    ).fetchall()


def resolver(conn: sqlite3.Connection, args: argparse.Namespace) -> dict:
    ensure_schema(conn)
    pedidos = conn.execute(
        """
        SELECT p.*
        FROM pedidos p
        WHERE p.empresa_id=? AND COALESCE(p.estado,'')='recibido_parcial'
          AND COALESCE(p.cancelado,0)=0 AND COALESCE(p.cerrado,0)=0
          AND NOT EXISTS (
              SELECT 1
              FROM simulador_resolucion_pedidos_parciales s
              WHERE s.corrida_id=? AND s.centro_id=? AND s.pedido_original_id=p.id
          )
        ORDER BY COALESCE(p.fecha_entrega, p.fecha_pedido), p.id
        LIMIT ?
        """,
        (EMPRESA_ID, args.corrida_id, args.centro_id, args.max_pedidos),
    ).fetchall()

    cerrados = rescates = lineas_rescate = 0
    importe_total = 0.0
    hoy = date.today().isoformat()
    for pedido in pedidos:
        faltas = pendientes(conn, int(pedido["id"]))
        importe = round(sum(max(0, float(l["cantidad_solicitada"] or 0) - float(l["cantidad_recibida"] or 0)) * float(l["precio_unitario"] or 0) for l in faltas), 2)
        incidencia_id = crear_incidencia(conn, pedido, importe, len(faltas))
        pedido_rescate_id = None
        decision = "cerrado_por_diferencia_menor"

        if faltas and importe >= args.min_importe_rescate:
            numero = f"RESCATE-H1-{date.today().strftime('%Y%m%d')}-{pedido['id']:05d}"
            cur = conn.execute(
                """
                INSERT INTO pedidos
                (numero_pedido, proveedor_id, fecha_pedido, fecha_entrega, estado,
                 observaciones, creado_por, empresa_id, departamento_solicitante_id,
                 departamento_id, tipo_pedido, origen_documento, validado_economicamente)
                VALUES (?, ?, ?, ?, 'enviado', ?, 'Simulador rescate compras', ?,
                        ?, ?, 'rescate_pedido_parcial', 'simulador_rescate_parcial', 0)
                """,
                (
                    numero,
                    pedido["proveedor_id"],
                    hoy,
                    (date.today() + timedelta(days=1)).isoformat(),
                    f"Rescate automatico por pedido parcial {pedido['numero_pedido']}.",
                    EMPRESA_ID,
                    pedido["departamento_solicitante_id"],
                    pedido["departamento_id"],
                ),
            )
            pedido_rescate_id = int(cur.lastrowid)
            rescates += 1
            for linea in faltas:
                pendiente = round(float(linea["cantidad_solicitada"] or 0) - float(linea["cantidad_recibida"] or 0), 3)
                if pendiente <= 0:
                    continue
                conn.execute(
                    """
                    INSERT INTO lineas_pedido
                    (pedido_id, producto_id, cantidad_solicitada, cantidad_recibida, precio_unitario)
                    VALUES (?, ?, ?, 0, ?)
                    """,
                    (pedido_rescate_id, linea["producto_id"], pendiente, linea["precio_unitario"]),
                )
                lineas_rescate += 1
            decision = "rescate_generado"

        conn.execute(
            """
            UPDATE pedidos
            SET cerrado=1, estado='cerrado', fecha_cierre=CURRENT_TIMESTAMP,
                observaciones=TRIM(COALESCE(observaciones,'') || ' | Cerrado por simulador tras recepcion parcial; ver incidencia/propuesta de rescate.')
            WHERE id=?
            """,
            (pedido["id"],),
        )
        conn.execute(
            """
            INSERT INTO simulador_resolucion_pedidos_parciales
            (corrida_id, centro_id, pedido_original_id, pedido_rescate_id, fecha,
             proveedor_id, lineas_pendientes, importe_pendiente, decision,
             incidencia_id, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                args.corrida_id,
                args.centro_id,
                pedido["id"],
                pedido_rescate_id,
                hoy,
                pedido["proveedor_id"],
                len(faltas),
                importe,
                decision,
                incidencia_id,
                EMPRESA_ID,
            ),
        )
        log_event(conn, args, int(pedido["id"]), hoy, decision, {"importe_pendiente": importe, "pedido_rescate_id": pedido_rescate_id})
        cerrados += 1
        importe_total += importe

    conn.commit()
    return {
        "parciales_objetivo": len(pedidos),
        "cerrados": cerrados,
        "rescates_generados": rescates,
        "lineas_rescate": lineas_rescate,
        "importe_pendiente": round(importe_total, 2),
    }


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        print(json.dumps(resolver(conn, args), ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

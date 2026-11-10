"""Recepciona pedidos pendientes del simulador.

El simulador crea pedidos desde varias vias: mise en place, necesidades,
stock automatico y compras por familias. Esta herramienta los pasa por el
circuito real de recepcion para que compras, almacenes, APPCC y costes tengan
datos conectados.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DEFAULT = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID_DEFAULT = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--corrida-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID_DEFAULT)
    parser.add_argument("--seed", type=int, default=20260604)
    parser.add_argument("--max-pedidos", type=int, default=90)
    parser.add_argument(
        "--prefijos",
        default="COMPRA-H1-,AUTO-H1-,PED-VIDA-H1-,ALERTA-MISE-H1-,BAJO-MIN-H1-,RESCATE-H1-,PED-",
        help="Prefijos separados por coma. Vacio = cualquier pedido pendiente.",
    )
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def import_stock_helper(db_path: Path):
    os.environ["ERP_DATABASE"] = str(db_path)
    os.environ["PYTHONPATH"] = str(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from blueprints.compras.routes import _aplicar_stock_recepcion

    return _aplicar_stock_recepcion


def log_event(
    conn: sqlite3.Connection,
    corrida_id: int,
    centro_id: int,
    fecha: str,
    tipo: str,
    severidad: str,
    descripcion: str,
    tabla_ref: str,
    registro_id: int | None,
    impacto: dict,
) -> None:
    if not table_exists(conn, "simulador_vida_eventos"):
        return
    conn.execute(
        """
        INSERT INTO simulador_vida_eventos
        (corrida_id, fecha_simulada, modulo, tipo, severidad, descripcion,
         tabla_ref, registro_id, impacto_json, empresa_id, centro_id)
        VALUES (?, ?, 'compras', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            corrida_id,
            fecha,
            tipo,
            severidad,
            descripcion,
            tabla_ref,
            registro_id,
            json.dumps(impacto, ensure_ascii=False),
            EMPRESA_ID,
            centro_id,
        ),
    )


def almacen_producto(conn: sqlite3.Connection, producto_id: int, centro_id: int) -> int | None:
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
        (EMPRESA_ID, centro_id, centro_id),
    ).fetchone()
    return int(row["id"]) if row else None


def temperatura_recepcion(producto: sqlite3.Row, forzar_mala: bool) -> float:
    tipo = (producto["tipo_conservacion"] or "").lower()
    fam = (producto["familia"] or "").lower()
    if "congel" in tipo:
        return round(random.uniform(-18.5, -15.0), 1) if not forzar_mala else round(random.uniform(-9, -3), 1)
    if "refriger" in tipo or "pesc" in fam or "carne" in fam or "lact" in fam:
        return round(random.uniform(0.5, 4.0), 1) if not forzar_mala else round(random.uniform(7, 11), 1)
    return round(random.uniform(12, 20), 1)


def incidencia(conn: sqlite3.Connection, recepcion_id: int, descripcion: str, gravedad: str, departamento_id: int | None) -> int | None:
    if not table_exists(conn, "incidencias"):
        return None
    conn.execute(
        """
        INSERT INTO incidencias
        (tipo, referencia_id, fecha, usuario, descripcion, gravedad, estado,
         empresa_id, origen_modulo, destino_modulo, categoria_operativa,
         departamento_id, requiere_appcc, origen_tabla, origen_id)
        VALUES ('recepcion', ?, CURRENT_TIMESTAMP, 'Simulador recepcion general', ?, ?, 'abierta',
                ?, 'compras', 'compras', 'recepcion_compra', ?, 1, 'recepciones', ?)
        """,
        (recepcion_id, descripcion, gravedad, EMPRESA_ID, departamento_id, recepcion_id),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def pedido_entrega(pedido: sqlite3.Row) -> str:
    if pedido["fecha_entrega"]:
        return str(pedido["fecha_entrega"])[:10]
    if pedido["fecha_pedido"]:
        return (date.fromisoformat(str(pedido["fecha_pedido"])[:10]) + timedelta(days=1)).isoformat()
    return date.today().isoformat()


def seleccionar_pedidos(conn: sqlite3.Connection, prefijos: list[str], max_pedidos: int) -> list[sqlite3.Row]:
    params: list[object] = [EMPRESA_ID]
    filtro_prefijo = ""
    if prefijos:
        condiciones = []
        for prefijo in prefijos:
            condiciones.append("p.numero_pedido LIKE ?")
            params.append(f"{prefijo}%")
        filtro_prefijo = "AND (" + " OR ".join(condiciones) + ")"

    rows = conn.execute(
        f"""
        SELECT p.*
        FROM pedidos p
        WHERE p.empresa_id=?
          {filtro_prefijo}
          AND COALESCE(p.cancelado,0)=0
          AND COALESCE(p.cerrado,0)=0
          AND COALESCE(p.estado,'') NOT IN ('cancelado','cerrado','recibido','facturado')
          AND NOT EXISTS (
              SELECT 1 FROM recepciones r
              WHERE r.pedido_id=p.id AND r.empresa_id=p.empresa_id
          )
          AND EXISTS (SELECT 1 FROM lineas_pedido lp WHERE lp.pedido_id=p.id)
        ORDER BY COALESCE(p.fecha_entrega, p.fecha_pedido), p.id
        """,
        params,
    ).fetchall()
    return rows[:max_pedidos] if max_pedidos and max_pedidos > 0 else rows


def recepcionar(conn: sqlite3.Connection, db_path: Path, args: argparse.Namespace) -> dict:
    aplicar_stock = import_stock_helper(db_path)
    prefijos = [p.strip() for p in (args.prefijos or "").split(",") if p.strip()]
    pedidos = seleccionar_pedidos(conn, prefijos, args.max_pedidos)

    resultado = {
        "pedidos_objetivo": len(pedidos),
        "recepciones_creadas": 0,
        "lineas_recepcion": 0,
        "incidencias": 0,
        "pedidos_parciales": 0,
        "lineas_rechazadas": 0,
        "stock_aplicado_lineas": 0,
    }

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

        entrega = pedido_entrega(pedido)
        origen = (pedido["origen_documento"] or pedido["tipo_pedido"] or pedido["numero_pedido"] or "").lower()
        fallo_prob = 0.18
        if "stock" in origen or str(pedido["numero_pedido"]).startswith("AUTO-H1-"):
            fallo_prob = 0.12
        elif "carn" in origen or "pesc" in origen:
            fallo_prob = 0.28
        fallo_pedido = random.random() < fallo_prob

        conn.execute(
            """
            INSERT INTO recepciones
            (pedido_id, proveedor_id, fecha_recepcion, numero_albaran, observaciones,
             recibido_por, empresa_id, tipo_recepcion, departamento_receptor_id,
             validada_operativamente, validada_economicamente, stock_aplicado,
             departamento_id, estado_facturacion)
            VALUES (?, ?, ?, ?, ?, 'Simulador recepcion general', ?, 'pedido', ?, 1, 0, 0, ?, 'pendiente_factura')
            """,
            (
                pedido["id"],
                pedido["proveedor_id"],
                entrega,
                f"ALB-{pedido['numero_pedido']}",
                "Recepcion simulada con control de cantidades, temperatura y APPCC.",
                EMPRESA_ID,
                pedido["departamento_id"],
                pedido["departamento_id"],
            ),
        )
        recepcion_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        resultado["recepciones_creadas"] += 1
        pedido_parcial = False
        incidencias_pedido = 0

        for linea in lineas:
            solicitada = float(linea["cantidad_solicitada"] or 0)
            ya_recibida = float(linea["cantidad_recibida"] or 0)
            pendiente = max(0.0, solicitada - ya_recibida)
            if pendiente <= 0:
                continue

            modo = "ok"
            if fallo_pedido:
                modo = random.choices(["ok", "parcial", "faltante", "temperatura"], weights=[65, 18, 7, 10], k=1)[0]
            cantidad = pendiente
            aceptado = 1
            motivo = None
            inc_id = None

            if modo == "parcial":
                cantidad = round(pendiente * random.uniform(0.50, 0.85), 3)
                motivo = f"Entrega parcial: faltan {pendiente - cantidad:.3f} {linea['unidad'] or ''} de {linea['nombre']}."
                inc_id = incidencia(conn, recepcion_id, motivo, "media", pedido["departamento_id"])
                incidencias_pedido += 1
                pedido_parcial = True
            elif modo == "faltante":
                cantidad = 0.0
                aceptado = 0
                motivo = f"Proveedor no entrega {linea['nombre']}."
                inc_id = incidencia(conn, recepcion_id, motivo, "alta", pedido["departamento_id"])
                incidencias_pedido += 1
                pedido_parcial = True
                resultado["lineas_rechazadas"] += 1
            elif modo == "temperatura":
                aceptado = 0
                motivo = f"Rechazado por temperatura fuera de rango: {linea['nombre']}."
                inc_id = incidencia(conn, recepcion_id, motivo, "alta", pedido["departamento_id"])
                incidencias_pedido += 1
                pedido_parcial = True
                resultado["lineas_rechazadas"] += 1

            almacen_id = almacen_producto(conn, int(linea["producto_id"]), args.centro_id)
            temp = temperatura_recepcion(linea, modo == "temperatura")
            cad = (date.fromisoformat(entrega) + timedelta(days=random.randint(4, 75))).isoformat()
            lote = f"SIM-{pedido['id']}-{linea['producto_id']}-{linea['id']}"
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
            resultado["lineas_recepcion"] += 1

        aplicadas = aplicar_stock(conn.cursor(), recepcion_id, EMPRESA_ID, "Simulador recepcion general")
        resultado["stock_aplicado_lineas"] += int(aplicadas or 0)
        resultado["incidencias"] += incidencias_pedido
        if pedido_parcial:
            resultado["pedidos_parciales"] += 1

        estado = "recibido_parcial" if pedido_parcial else "recibido"
        estado_recepcion = "parcial" if pedido_parcial else "recibido"
        conn.execute(
            """
            UPDATE pedidos
            SET estado=?, estado_recepcion=?, fecha_recepcion=?, fecha_envio=COALESCE(fecha_envio, ?),
                validado_economicamente=0, cerrado=0
            WHERE id=?
            """,
            (estado, estado_recepcion, entrega, f"{pedido['fecha_pedido']} 10:00:00", pedido["id"]),
        )
        log_event(
            conn,
            args.corrida_id,
            args.centro_id,
            entrega,
            "recepcion_pedido_simulada",
            "alta" if pedido_parcial else "info",
            f"Recepcion {pedido['numero_pedido']}: {estado_recepcion}, incidencias {incidencias_pedido}.",
            "recepciones",
            recepcion_id,
            {"pedido_id": pedido["id"], "parcial": pedido_parcial, "stock_aplicado": aplicadas},
        )

    conn.commit()
    return resultado


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        print(json.dumps(recepcionar(conn, db_path, args), ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

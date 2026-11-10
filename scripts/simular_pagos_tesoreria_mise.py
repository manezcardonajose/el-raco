"""Simula pagos de facturas MISE-H1 y tension de tesoreria.

Parte de los documentos se pagan, otros quedan parciales y algunos se dejan
pendientes para probar vencimientos, caja y alertas de direccion.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import date
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
    parser.add_argument("--fecha-caja", default="2026-06-30")
    parser.add_argument("--seed", type=int, default=20260630)
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
        VALUES (?, ?, 'contabilidad', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (corrida_id, fecha, tipo, severidad, descripcion, tabla_ref, registro_id, json.dumps(impacto, ensure_ascii=False), EMPRESA_ID, CENTRO_ID),
    )


def load_docs(conn: sqlite3.Connection, prefijo: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT d.*, p.numero_pedido, pr.nombre proveedor_nombre
        FROM contabilidad_documentos d
        JOIN recepciones r ON r.id=d.origen_id
        JOIN pedidos p ON p.id=r.pedido_id
        LEFT JOIN proveedores pr ON pr.id=d.proveedor_id
        WHERE d.empresa_id=?
          AND d.origen_modulo='compras'
          AND d.origen_tipo='recepcion'
          AND p.numero_pedido LIKE ?
          AND d.estado IN ('pendiente_pago','pagado_parcial','vencido')
          AND COALESCE(d.pendiente,0)>0
          AND NOT EXISTS (
              SELECT 1
              FROM contabilidad_pagos pg
              WHERE pg.documento_id=d.id
                AND pg.empresa_id=d.empresa_id
                AND COALESCE(pg.referencia,'') LIKE 'PAGO-MISE-%'
          )
        ORDER BY d.fecha_vencimiento, d.id
        """,
        (EMPRESA_ID, f"{prefijo}%"),
    ).fetchall()


def nuevo_estado(pendiente: float, pagado: float, fecha_vencimiento: str | None, fecha_caja: str) -> str:
    if pendiente <= 0.009:
        return "pagado"
    if pagado > 0:
        return "pagado_parcial"
    if fecha_vencimiento and str(fecha_vencimiento)[:10] < fecha_caja:
        return "vencido"
    return "pendiente_pago"


def simular(conn: sqlite3.Connection, args: argparse.Namespace) -> dict:
    docs = load_docs(conn, args.prefijo)
    pagados = 0
    parciales = 0
    vencidos = 0
    pagos_creados = 0
    salida_caja = 0.0

    for idx, doc in enumerate(docs, start=1):
        pendiente_ini = float(doc["pendiente"] or 0)
        if pendiente_ini <= 0:
            continue
        vencida = bool(doc["fecha_vencimiento"] and str(doc["fecha_vencimiento"])[:10] < args.fecha_caja)
        modo = random.choices(
            ["pago_total", "pago_parcial", "retener"],
            weights=[54 if vencida else 38, 28, 18 if vencida else 34],
            k=1,
        )[0]
        importe = 0.0
        if modo == "pago_total":
            importe = pendiente_ini
        elif modo == "pago_parcial":
            importe = round(pendiente_ini * random.uniform(0.35, 0.72), 2)

        if importe > 0:
            referencia = f"PAGO-MISE-{doc['id']}-{idx:03d}"
            metodo = random.choices(["transferencia", "confirming", "tarjeta_empresa"], weights=[70, 22, 8], k=1)[0]
            cuenta = "572" if metodo in {"transferencia", "confirming"} else "520"
            conn.execute(
                """
                INSERT INTO contabilidad_pagos
                (documento_id, fecha_pago, metodo, importe, cuenta_tesoreria,
                 referencia, observaciones, usuario, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'Simulador pagos MISE', ?)
                """,
                (
                    doc["id"],
                    args.fecha_caja,
                    metodo,
                    importe,
                    cuenta,
                    referencia,
                    f"Pago simulado de tesoreria MISE para {doc['numero']}.",
                    EMPRESA_ID,
                ),
            )
            pagos_creados += 1
            salida_caja += importe

        pagado_total = round(float(doc["pagado"] or 0) + importe, 2)
        pendiente = round(max(0.0, pendiente_ini - importe), 2)
        estado = nuevo_estado(pendiente, pagado_total, doc["fecha_vencimiento"], args.fecha_caja)
        if estado == "pagado":
            pagados += 1
        elif estado == "pagado_parcial":
            parciales += 1
        elif estado == "vencido":
            vencidos += 1

        conn.execute(
            """
            UPDATE contabilidad_documentos
            SET pagado=?, pendiente=?, estado=?
            WHERE id=? AND empresa_id=?
            """,
            (pagado_total, pendiente, estado, doc["id"], EMPRESA_ID),
        )
        log_event(
            conn,
            args.corrida_id,
            args.fecha_caja,
            "pago_tesoreria_mise",
            "alta" if estado in {"vencido", "pagado_parcial"} else "info",
            f"{doc['numero']}: estado {estado}, pagado {pagado_total:.2f}, pendiente {pendiente:.2f}.",
            "contabilidad_documentos",
            int(doc["id"]),
            {
                "pedido": doc["numero_pedido"],
                "proveedor": doc["proveedor_nombre"],
                "modo": modo,
                "importe_pago": round(importe, 2),
            },
        )

    conn.commit()
    return {
        "documentos_tratados": len(docs),
        "pagos_creados": pagos_creados,
        "pagados": pagados,
        "parciales": parciales,
        "vencidos": vencidos,
        "salida_caja": round(salida_caja, 2),
    }


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        print(json.dumps(simular(conn, args), ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

"""Valida economicamente recepciones MISE-H1 del simulador.

Convierte recepciones operativas en documentos de compra y documentos
contables, simulando facturas/albaranes con pequenas desviaciones de precio.
No borra nada y salta las recepciones ya tratadas.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import date, timedelta
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
    parser.add_argument("--max-recepciones", type=int, default=0)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def log_event(
    conn: sqlite3.Connection,
    corrida_id: int,
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
            CENTRO_ID,
        ),
    )


def ensure_tercero(conn: sqlite3.Connection, proveedor: sqlite3.Row) -> int | None:
    if not proveedor:
        return None
    row = conn.execute(
        """
        SELECT id
        FROM contabilidad_terceros
        WHERE empresa_id=? AND proveedor_id=?
        ORDER BY id
        LIMIT 1
        """,
        (EMPRESA_ID, proveedor["id"]),
    ).fetchone()
    if row:
        return int(row["id"])
    conn.execute(
        """
        INSERT INTO contabilidad_terceros
        (tipo, nombre, nif, email, telefono, codigo_contable, proveedor_id, activo, empresa_id, centro_id)
        VALUES ('proveedor', ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            proveedor["nombre"],
            proveedor["nif"],
            proveedor["email"],
            proveedor["telefono"],
            f"400{int(proveedor['id']):05d}",
            proveedor["id"],
            EMPRESA_ID,
            proveedor["centro_id"] or CENTRO_ID,
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def estado_facturacion(tipo_documento: str) -> str:
    tipo = (tipo_documento or "albaran").strip().lower()
    if tipo in {"factura", "ticket", "factura_simplificada"}:
        return "factura_recibida"
    return "pendiente_factura"


def cargar_recepciones(conn: sqlite3.Connection, prefijo: str, limite: int) -> list[sqlite3.Row]:
    sql = """
        SELECT r.*, p.numero_pedido, p.departamento_id pedido_departamento_id,
               pr.nombre proveedor_nombre, pr.nif, pr.email, pr.telefono,
               pr.forma_pago, pr.dias_pago, pr.documento_habitual,
               pr.facturacion_habitual, pr.centro_id proveedor_centro_id
        FROM recepciones r
        JOIN pedidos p ON p.id=r.pedido_id
        LEFT JOIN proveedores pr ON pr.id=r.proveedor_id
        WHERE r.empresa_id=?
          AND p.numero_pedido LIKE ?
          AND COALESCE(r.validada_operativamente,0)=1
          AND COALESCE(r.validada_economicamente,0)=0
          AND COALESCE(r.contabilidad_documento_id,0)=0
          AND NOT EXISTS (
              SELECT 1
              FROM documentos_compra dc
              WHERE dc.empresa_id=r.empresa_id
                AND dc.recepcion_id=r.id
                AND dc.origen_subida='simulador_mise_economica'
          )
        ORDER BY r.fecha_recepcion, p.numero_pedido
    """
    rows = conn.execute(sql, (EMPRESA_ID, f"{prefijo}%")).fetchall()
    if limite and limite > 0:
        return rows[:limite]
    return rows


def lineas_aceptadas(conn: sqlite3.Connection, recepcion_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT lr.*, p.nombre producto_nombre, p.unidad
        FROM lineas_recepcion lr
        LEFT JOIN productos p ON p.id=lr.producto_id
        WHERE lr.recepcion_id=?
          AND COALESCE(lr.aceptado,1)=1
          AND COALESCE(lr.cantidad_recibida,0)>0
        ORDER BY lr.id
        """,
        (recepcion_id,),
    ).fetchall()


def proveedor_row(recepcion: sqlite3.Row) -> dict:
    return {
        "id": recepcion["proveedor_id"],
        "nombre": recepcion["proveedor_nombre"],
        "nif": recepcion["nif"],
        "email": recepcion["email"],
        "telefono": recepcion["telefono"],
        "centro_id": recepcion["proveedor_centro_id"],
    }


def validar(conn: sqlite3.Connection, args: argparse.Namespace) -> dict:
    recepciones = cargar_recepciones(conn, args.prefijo, args.max_recepciones)
    creadas = 0
    docs_compra = 0
    docs_contables = 0
    lineas_contables = 0
    bloqueadas = 0
    importe_total = 0.0

    for idx, recepcion in enumerate(recepciones, start=1):
        lineas = lineas_aceptadas(conn, int(recepcion["id"]))
        if not lineas:
            log_event(
                conn,
                args.corrida_id,
                recepcion["fecha_recepcion"],
                "validacion_economica_mise_sin_lineas",
                "alta",
                f"Recepcion {recepcion['numero_albaran']} sin lineas aceptadas para validar.",
                "recepciones",
                int(recepcion["id"]),
                {"pedido": recepcion["numero_pedido"]},
            )
            continue

        base_operativa = sum(float(l["importe_total"] or 0) for l in lineas)
        desviacion_pct = random.choices(
            [-0.025, -0.01, 0.0, 0.012, 0.035, 0.085],
            weights=[8, 15, 45, 18, 10, 4],
            k=1,
        )[0]
        base_documento = round(base_operativa * (1 + desviacion_pct), 2)
        impuesto = round(base_documento * 0.10, 2)
        total = round(base_documento + impuesto, 2)
        diferencia = round(base_documento - base_operativa, 2)
        bloqueada = abs(desviacion_pct) >= 0.06
        tipo_documento = random.choices(["factura", "albaran", "ticket"], weights=[62, 32, 6], k=1)[0]
        if bloqueada:
            tipo_documento = "factura"
        estado_fac = estado_facturacion(tipo_documento)
        fecha_doc = recepcion["fecha_recepcion"] or date.today().isoformat()
        dias_pago = int(recepcion["dias_pago"] or 30)
        fecha_venc = (date.fromisoformat(str(fecha_doc)[:10]) + timedelta(days=dias_pago)).isoformat()
        numero_doc = f"{'FAC' if tipo_documento == 'factura' else 'ALB' if tipo_documento == 'albaran' else 'TCK'}-ECO-{recepcion['numero_pedido']}"

        texto = (
            f"Validacion economica MISE. Pedido {recepcion['numero_pedido']}. "
            f"Base recepcion {base_operativa:.2f}. Base documento {base_documento:.2f}. "
            f"Diferencia {diferencia:.2f}."
        )
        conn.execute(
            """
            INSERT INTO documentos_compra
            (proveedor_id, pedido_id, recepcion_id, tipo_documento, nombre_archivo,
             ruta_archivo, mime_type, origen_subida, fecha_documento, numero_documento,
             procesado_ia, texto_extraido, validado, subido_por, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, 'text/plain', 'simulador_mise_economica', ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                recepcion["proveedor_id"],
                recepcion["pedido_id"],
                recepcion["id"],
                tipo_documento,
                f"{numero_doc}.txt",
                f"simulador/mise/{numero_doc}.txt",
                fecha_doc,
                numero_doc,
                texto,
                0 if bloqueada else 1,
                "Simulador validacion economica MISE",
                EMPRESA_ID,
            ),
        )
        doc_compra_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        docs_compra += 1

        tercero_id = ensure_tercero(conn, proveedor_row(recepcion))
        cont_estado = "bloqueado" if bloqueada else ("pendiente_pago" if tipo_documento != "albaran" else "pendiente_factura")
        tipo_contable = "ticket" if tipo_documento == "ticket" else ("factura_compra" if tipo_documento == "factura" else "albaran")
        departamento_id = recepcion["departamento_receptor_id"] or recepcion["departamento_id"] or recepcion["pedido_departamento_id"]
        conn.execute(
            """
            INSERT INTO contabilidad_documentos
            (tipo, numero, tercero_id, proveedor_id, fecha_documento, fecha_vencimiento,
             estado, base_imponible, impuesto_importe, total, pagado, pendiente,
             moneda, cuenta_contable, centro_coste, departamento_id, subdepartamento_id,
             punto_venta_id, origen_modulo, origen_tipo, origen_id, archivo,
             observaciones, creado_por, fecha_creacion, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'EUR', '600', ?, ?, ?, ?,
                    'compras', 'recepcion', ?, ?, ?, 'Simulador validacion economica MISE',
                    CURRENT_TIMESTAMP, ?, ?)
            """,
            (
                tipo_contable,
                numero_doc,
                tercero_id,
                recepcion["proveedor_id"],
                fecha_doc,
                fecha_venc,
                cont_estado,
                base_documento,
                impuesto,
                total,
                total,
                f"MISE-H1-{recepcion['pedido_id']}",
                departamento_id,
                recepcion["subdepartamento_receptor_id"],
                recepcion["punto_venta_id"],
                recepcion["id"],
                f"simulador/mise/{numero_doc}.txt",
                texto,
                EMPRESA_ID,
                CENTRO_ID,
            ),
        )
        contabilidad_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        docs_contables += 1

        factor = base_documento / base_operativa if base_operativa else 1.0
        for linea in lineas:
            linea_base = round(float(linea["importe_total"] or 0) * factor, 2)
            linea_total = round(linea_base * 1.10, 2)
            conn.execute(
                """
                INSERT INTO contabilidad_lineas_documento
                (documento_id, concepto, cantidad, precio_unitario, base, impuesto_tipo,
                 impuesto_importe, total, cuenta_contable, departamento_id,
                 subdepartamento_id, punto_venta_id, empresa_id)
                VALUES (?, ?, ?, ?, ?, 10, ?, ?, '600', ?, ?, ?, ?)
                """,
                (
                    contabilidad_id,
                    linea["producto_nombre"] or f"Producto {linea['producto_id']}",
                    float(linea["cantidad_recibida"] or 0),
                    float(linea["precio_unitario"] or 0) * factor,
                    linea_base,
                    round(linea_total - linea_base, 2),
                    linea_total,
                    departamento_id,
                    recepcion["subdepartamento_receptor_id"],
                    recepcion["punto_venta_id"],
                    EMPRESA_ID,
                ),
            )
            lineas_contables += 1

        conn.execute(
            """
            UPDATE recepciones
            SET validada_economicamente=?,
                documento_adjunto_id=COALESCE(documento_adjunto_id, ?),
                documento_economico_tipo=?,
                estado_facturacion=?,
                fecha_vencimiento_economica=?,
                forma_pago_economica=?,
                contabilidad_documento_id=?
            WHERE id=? AND empresa_id=?
            """,
            (
                0 if bloqueada else 1,
                doc_compra_id,
                tipo_documento,
                "bloqueado_revision" if bloqueada else estado_fac,
                fecha_venc,
                recepcion["forma_pago"] or "transferencia",
                contabilidad_id,
                recepcion["id"],
                EMPRESA_ID,
            ),
        )
        if not bloqueada:
            conn.execute(
                """
                UPDATE pedidos
                SET validado_economicamente=1, validado_por='Simulador validacion economica MISE',
                    fecha_validacion=CURRENT_TIMESTAMP
                WHERE id=? AND empresa_id=?
                """,
                (recepcion["pedido_id"], EMPRESA_ID),
            )
        else:
            bloqueadas += 1
            conn.execute(
                """
                UPDATE pedidos
                SET bloqueado_contabilidad=1
                WHERE id=? AND empresa_id=?
                """,
                (recepcion["pedido_id"], EMPRESA_ID),
            )

        importe_total += total
        creadas += 1
        log_event(
            conn,
            args.corrida_id,
            fecha_doc,
            "validacion_economica_mise",
            "alta" if bloqueada else "info",
            f"{numero_doc}: total {total:.2f} EUR, diferencia base {diferencia:.2f} EUR.",
            "contabilidad_documentos",
            contabilidad_id,
            {
                "pedido": recepcion["numero_pedido"],
                "recepcion_id": recepcion["id"],
                "documento_compra_id": doc_compra_id,
                "desviacion_pct": round(desviacion_pct * 100, 2),
                "bloqueada": bloqueada,
                "orden": idx,
            },
        )

    conn.commit()
    return {
        "recepciones_validadas": creadas,
        "documentos_compra": docs_compra,
        "documentos_contables": docs_contables,
        "lineas_contables": lineas_contables,
        "bloqueadas_revision": bloqueadas,
        "importe_total": round(importe_total, 2),
        "pendientes_no_tratadas": max(0, len(recepciones) - creadas),
    }


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        result = validar(conn, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

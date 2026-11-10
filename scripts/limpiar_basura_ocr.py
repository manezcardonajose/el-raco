#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Limpieza segura de basura OCR/IA en Nexora Kitchen.

Uso recomendado desde la raíz del proyecto:
    python tools/limpiar_basura_ocr.py --analizar
    python tools/limpiar_basura_ocr.py --ejecutar

Por defecto NO borra nada. Primero muestra lo que va a limpiar.
Antes de ejecutar crea copia automática de la base de datos.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable


def conectar(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def tabla_existe(cur: sqlite3.Cursor, tabla: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (tabla,),
    ).fetchone() is not None


def columnas(cur: sqlite3.Cursor, tabla: str) -> set[str]:
    if not tabla_existe(cur, tabla):
        return set()
    return {row["name"] for row in cur.execute(f"PRAGMA table_info({tabla})").fetchall()}


def backup_db(db_path: str) -> str:
    origen = Path(db_path)
    carpeta = origen.parent / "backups"
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{origen.stem}_backup_antes_limpieza_ocr_{datetime.now().strftime('%Y%m%d_%H%M%S')}{origen.suffix}"
    shutil.copy2(origen, destino)
    return str(destino)


def normalizar(txt: str | None) -> str:
    if not txt:
        return ""
    return re.sub(r"\s+", " ", str(txt).strip())


def nombre_sospechoso(nombre: str | None) -> bool:
    n = normalizar(nombre).upper()
    if not n:
        return True

    basura = [
        "PDF_ESCANEADO", "OCR_SIN_TEXTO", "ERROR_OCR", "NECESITA OCR",
        "LMPLIFICADA", "FACTURA SIMPLIFICADA", "IMPORTE . EUR", "TOTAL €",
        "ENTREGADO", "CAMBIO", "TARJETA", "AUTORIZACION", "APLICACION",
        "NUM.TRANSACC", "OPERACION", "CLIENTES VARIOS", "NIF", "NRO.",
        "FECHA", "CAJERO", "DOCUMENTO", "VENDEDOR", "TELEFONO", "BASE IMP",
    ]
    if any(b in n for b in basura):
        return True

    # Mucha mezcla de símbolos típica del OCR roto.
    simbolos = sum(1 for c in n if not (c.isalnum() or c.isspace() or c in "ÑÁÉÍÓÚÜ.,-/+%()"))
    if simbolos >= 2:
        return True

    # Texto muy corto o casi todo números.
    letras = sum(1 for c in n if c.isalpha())
    digitos = sum(1 for c in n if c.isdigit())
    if letras < 3:
        return True
    if digitos > letras and letras < 8:
        return True

    # Muchas minúsculas mezcladas en nombres importados desde OCR suelen indicar basura,
    # pero no lo usamos como criterio único para no borrar productos reales.
    return False


def proveedor_sospechoso(nombre: str | None) -> bool:
    n = normalizar(nombre).upper()
    if not n:
        return True
    if n == "PENDIENTE IA":
        return False  # se conserva como proveedor comodín controlado
    if nombre_sospechoso(n):
        return True
    if len(n) < 4:
        return True
    return False


def ids(sql_rows: Iterable[sqlite3.Row]) -> list[int]:
    return [int(r[0]) for r in sql_rows]


def placeholders(seq: Iterable[int]) -> str:
    seq = list(seq)
    return ",".join("?" for _ in seq) if seq else "NULL"


def contar(cur: sqlite3.Cursor, tabla: str, where: str = "1=1", params: tuple = ()) -> int:
    if not tabla_existe(cur, tabla):
        return 0
    return int(cur.execute(f"SELECT COUNT(*) FROM {tabla} WHERE {where}", params).fetchone()[0])


def buscar_documentos_ocr(cur: sqlite3.Cursor, desde: str, incluir_validadas: bool) -> list[int]:
    if not tabla_existe(cur, "documentos_compra"):
        return []

    where = [
        "COALESCE(dc.procesado_ia, 0) = 1",
        "(dc.tipo_documento IS NULL OR lower(dc.tipo_documento) IN ('albaran','ticket','factura','recepcion_directa'))",
    ]
    params: list[object] = []

    if tabla_existe(cur, "recepciones"):
        where.append("(r.fecha_recepcion IS NULL OR date(r.fecha_recepcion) >= date(?))")
        params.append(desde)

        if not incluir_validadas:
            where.append("COALESCE(r.validada_operativamente,0) = 0")
            where.append("COALESCE(r.validada_economicamente,0) = 0")
    else:
        if columnas(cur, "documentos_compra") & {"fecha_documento"}:
            where.append("(dc.fecha_documento IS NULL OR date(dc.fecha_documento) >= date(?))")
            params.append(desde)
        if not incluir_validadas:
            where.append("COALESCE(dc.validado,0) = 0")

    sql = """
        SELECT dc.id
        FROM documentos_compra dc
        LEFT JOIN recepciones r ON r.id = dc.recepcion_id
        WHERE """ + " AND ".join(where) + """
        ORDER BY dc.id
    """
    return ids(cur.execute(sql, tuple(params)).fetchall())


def buscar_recepciones_por_documentos(cur: sqlite3.Cursor, doc_ids: list[int]) -> list[int]:
    if not doc_ids or not tabla_existe(cur, "recepciones"):
        return []
    ph = placeholders(doc_ids)
    rows = cur.execute(
        f"""
        SELECT DISTINCT r.id
        FROM recepciones r
        WHERE r.documento_adjunto_id IN ({ph})
           OR r.id IN (SELECT recepcion_id FROM documentos_compra WHERE id IN ({ph}) AND recepcion_id IS NOT NULL)
        """,
        tuple(doc_ids + doc_ids),
    ).fetchall()
    return ids(rows)


def productos_creados_por_ocr(cur: sqlite3.Cursor, doc_ids: list[int]) -> set[int]:
    res: set[int] = set()
    if not doc_ids or not tabla_existe(cur, "documento_compra_lineas_detectadas"):
        return res
    ph = placeholders(doc_ids)
    for r in cur.execute(
        f"""
        SELECT DISTINCT producto_id
        FROM documento_compra_lineas_detectadas
        WHERE documento_id IN ({ph})
          AND producto_id IS NOT NULL
          AND lower(COALESCE(estado,'')) IN ('producto_creado','creado','nuevo')
        """,
        tuple(doc_ids),
    ):
        res.add(int(r["producto_id"]))
    return res


def productos_sospechosos_globales(cur: sqlite3.Cursor) -> set[int]:
    if not tabla_existe(cur, "productos"):
        return set()
    res: set[int] = set()
    for r in cur.execute("SELECT id, nombre FROM productos"):
        if nombre_sospechoso(r["nombre"]):
            res.add(int(r["id"]))
    return res


def referencias_producto(cur: sqlite3.Cursor, producto_id: int, ignorar_recepciones: set[int], ignorar_docs: set[int]) -> dict[str, int]:
    """Cuenta referencias que hacen peligroso borrar un producto."""
    refs: dict[str, int] = {}

    tablas_col = [
        ("recetas_ingredientes", "producto_id"),
        ("lineas_menu", "producto_id"),
        ("lineas_menu_diario", "producto_id"),
        ("consumo_produccion", "producto_id"),
        ("consumo_lotes", "producto_id"),
        ("lineas_pedido", "producto_id"),
        ("pedidos_programados", "producto_id"),
        ("producto_departamentos", "producto_id"),
        ("punto_venta_recetas", "producto_id"),
        ("producto_proveedor", "producto_id"),
        ("productos_proveedores", "producto_id"),
    ]

    for tabla, col in tablas_col:
        if not tabla_existe(cur, tabla) or col not in columnas(cur, tabla):
            continue
        n = contar(cur, tabla, f"{col}=?", (producto_id,))
        if n:
            refs[tabla] = n

    # Referencias que pueden ser solo de la basura que estamos borrando.
    if tabla_existe(cur, "lineas_recepcion"):
        if ignorar_recepciones:
            ph = placeholders(ignorar_recepciones)
            n = int(cur.execute(
                f"SELECT COUNT(*) FROM lineas_recepcion WHERE producto_id=? AND recepcion_id NOT IN ({ph})",
                (producto_id, *ignorar_recepciones),
            ).fetchone()[0])
        else:
            n = contar(cur, "lineas_recepcion", "producto_id=?", (producto_id,))
        if n:
            refs["lineas_recepcion_fuera_limpieza"] = n

    if tabla_existe(cur, "documento_compra_lineas_detectadas"):
        if ignorar_docs:
            ph = placeholders(ignorar_docs)
            n = int(cur.execute(
                f"SELECT COUNT(*) FROM documento_compra_lineas_detectadas WHERE producto_id=? AND documento_id NOT IN ({ph})",
                (producto_id, *ignorar_docs),
            ).fetchone()[0])
        else:
            n = contar(cur, "documento_compra_lineas_detectadas", "producto_id=?", (producto_id,))
        if n:
            refs["lineas_ia_fuera_limpieza"] = n

    # Stock/lotes/movimientos no impiden si solo proceden de la recepción que se limpia,
    # pero si hay consumo o cantidades actuales no cero se marca como referencia.
    if tabla_existe(cur, "lotes"):
        n = contar(cur, "lotes", "producto_id=? AND COALESCE(cantidad_actual,0) <> 0", (producto_id,))
        if n:
            refs["lotes_con_stock"] = n

    if tabla_existe(cur, "stock_almacen"):
        n = contar(cur, "stock_almacen", "producto_id=? AND ABS(COALESCE(cantidad,0)) > 0.0001", (producto_id,))
        if n:
            refs["stock_actual"] = n

    return refs


def proveedores_basura(cur: sqlite3.Cursor, recep_ids: set[int], doc_ids: set[int]) -> set[int]:
    if not tabla_existe(cur, "proveedores"):
        return set()

    candidatos: set[int] = set()
    for r in cur.execute("SELECT id, nombre FROM proveedores"):
        if proveedor_sospechoso(r["nombre"]):
            candidatos.add(int(r["id"]))

    seguros: set[int] = set()
    for pid in candidatos:
        refs = 0
        if tabla_existe(cur, "pedidos"):
            refs += contar(cur, "pedidos", "proveedor_id=?", (pid,))
        if tabla_existe(cur, "recepciones"):
            if recep_ids:
                ph = placeholders(recep_ids)
                refs += int(cur.execute(
                    f"SELECT COUNT(*) FROM recepciones WHERE proveedor_id=? AND id NOT IN ({ph})",
                    (pid, *recep_ids),
                ).fetchone()[0])
            else:
                refs += contar(cur, "recepciones", "proveedor_id=?", (pid,))
        if tabla_existe(cur, "documentos_compra"):
            if doc_ids:
                ph = placeholders(doc_ids)
                refs += int(cur.execute(
                    f"SELECT COUNT(*) FROM documentos_compra WHERE proveedor_id=? AND id NOT IN ({ph})",
                    (pid, *doc_ids),
                ).fetchone()[0])
            else:
                refs += contar(cur, "documentos_compra", "proveedor_id=?", (pid,))
        if tabla_existe(cur, "lotes"):
            refs += contar(cur, "lotes", "proveedor_id=?", (pid,))
        if tabla_existe(cur, "historico_precios"):
            refs += contar(cur, "historico_precios", "proveedor_id=?", (pid,))
        if refs == 0:
            seguros.add(pid)
    return seguros


def borrar_por_ids(cur: sqlite3.Cursor, tabla: str, campo: str, valores: set[int] | list[int]) -> int:
    valores = list(valores)
    if not valores or not tabla_existe(cur, tabla):
        return 0
    ph = placeholders(valores)
    cur.execute(f"DELETE FROM {tabla} WHERE {campo} IN ({ph})", tuple(valores))
    return cur.rowcount if cur.rowcount != -1 else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Limpieza segura de basura OCR/IA en Nexora Kitchen.")
    parser.add_argument("--db", default="pepe4.db", help="Ruta a la base de datos SQLite. Por defecto: pepe4.db")
    parser.add_argument("--desde", default="2026-04-24", help="Fecha mínima de recepciones/documentos a limpiar. Por defecto: 2026-04-24")
    parser.add_argument("--analizar", action="store_true", help="Solo muestra lo que se limpiaría. Es el modo por defecto.")
    parser.add_argument("--ejecutar", action="store_true", help="Ejecuta la limpieza real.")
    parser.add_argument("--incluir-validadas", action="store_true", help="También limpia recepciones/directas OCR ya validadas. Usar solo si son pruebas.")
    parser.add_argument("--limpiar-productos-sospechosos", action="store_true", help="Además de productos creados por OCR, intenta borrar productos sospechosos huérfanos.")
    args = parser.parse_args()

    db_path = args.db
    if not os.path.exists(db_path):
        alt = Path("instance") / db_path
        if alt.exists():
            db_path = str(alt)
        else:
            raise SystemExit(f"No encuentro la base de datos: {args.db}")

    con = conectar(db_path)
    cur = con.cursor()

    doc_ids = set(buscar_documentos_ocr(cur, args.desde, args.incluir_validadas))
    recep_ids = set(buscar_recepciones_por_documentos(cur, list(doc_ids)))

    prod_ids = productos_creados_por_ocr(cur, list(doc_ids))
    if args.limpiar_productos_sospechosos:
        prod_ids |= productos_sospechosos_globales(cur)

    # Solo productos sin referencias reales fuera de lo que se limpia.
    prod_borrables: set[int] = set()
    prod_retenidos: dict[int, dict[str, int]] = {}
    for pid in sorted(prod_ids):
        refs = referencias_producto(cur, pid, recep_ids, doc_ids)
        if refs:
            prod_retenidos[pid] = refs
        else:
            prod_borrables.add(pid)

    prov_ids = proveedores_basura(cur, recep_ids, doc_ids)

    print("\n=== LIMPIEZA OCR / IA NEXORA KITCHEN ===")
    print(f"Base de datos: {db_path}")
    print(f"Desde fecha:   {args.desde}")
    print(f"Incluir validadas: {args.incluir_validadas}")
    print("")
    print(f"Documentos OCR a limpiar:       {len(doc_ids)} -> {sorted(doc_ids)}")
    print(f"Recepciones directas a limpiar: {len(recep_ids)} -> {sorted(recep_ids)}")
    print(f"Productos borrables:            {len(prod_borrables)} -> {sorted(prod_borrables)}")
    print(f"Proveedores basura borrables:   {len(prov_ids)} -> {sorted(prov_ids)}")

    if prod_retenidos:
        print("\nProductos sospechosos RETENIDOS por tener referencias/stock:")
        for pid, refs in sorted(prod_retenidos.items()):
            nombre = cur.execute("SELECT nombre FROM productos WHERE id=?", (pid,)).fetchone()
            print(f"  - {pid} {nombre['nombre'] if nombre else ''}: {refs}")

    if not args.ejecutar:
        print("\nModo análisis. No se ha borrado nada.")
        print("Para ejecutar: python tools/limpiar_basura_ocr.py --ejecutar")
        print("Si también quieres borrar productos OCR huérfanos sospechosos:")
        print("python tools/limpiar_basura_ocr.py --ejecutar --limpiar-productos-sospechosos")
        return

    backup = backup_db(db_path)
    print(f"\nBackup creado: {backup}")

    try:
        con.execute("BEGIN")

        # Revertir stock de líneas de recepción que se vayan a borrar.
        if recep_ids and tabla_existe(cur, "lineas_recepcion"):
            for lr in cur.execute(
                f"SELECT * FROM lineas_recepcion WHERE recepcion_id IN ({placeholders(recep_ids)})",
                tuple(recep_ids),
            ).fetchall():
                producto_id = lr["producto_id"]
                almacen_id = lr["almacen_id"]
                cantidad = float(lr["cantidad_recibida"] or 0)
                if producto_id and almacen_id and cantidad and tabla_existe(cur, "stock_almacen"):
                    cur.execute(
                        """
                        UPDATE stock_almacen
                        SET cantidad = COALESCE(cantidad,0) - ?
                        WHERE producto_id=? AND almacen_id=?
                        """,
                        (cantidad, producto_id, almacen_id),
                    )

        # Borrado dependiente.
        borrar_por_ids(cur, "recepcion_validaciones", "recepcion_id", recep_ids)
        borrar_por_ids(cur, "lineas_recepcion", "recepcion_id", recep_ids)

        if doc_ids:
            borrar_por_ids(cur, "documento_compra_lineas_detectadas", "documento_id", doc_ids)

        # Movimientos/lotes creados por recepción directa OCR de prueba.
        if tabla_existe(cur, "movimientos_stock"):
            cur.execute(
                """
                DELETE FROM movimientos_stock
                WHERE lower(COALESCE(motivo,'')) LIKE '%recepción directa%'
                   OR lower(COALESCE(motivo,'')) LIKE '%recepcion directa%'
                """
            )

        if tabla_existe(cur, "lotes"):
            cur.execute(
                """
                DELETE FROM lotes
                WHERE lower(COALESCE(documento_origen,'')) IN ('recepcion_directa','recepción directa')
                """
            )

        borrar_por_ids(cur, "documentos_compra", "id", doc_ids)
        borrar_por_ids(cur, "recepciones", "id", recep_ids)

        # Limpieza de productos auxiliares.
        borrar_por_ids(cur, "producto_proveedor", "producto_id", prod_borrables)
        borrar_por_ids(cur, "productos_proveedores", "producto_id", prod_borrables)
        borrar_por_ids(cur, "stock_almacen", "producto_id", prod_borrables)
        borrar_por_ids(cur, "productos", "id", prod_borrables)

        # Proveedores basura sin referencias.
        borrar_por_ids(cur, "proveedores", "id", prov_ids)

        # Stock cero sobrante.
        if tabla_existe(cur, "stock_almacen"):
            cur.execute("DELETE FROM stock_almacen WHERE ABS(COALESCE(cantidad,0)) < 0.0001 AND COALESCE(stock_minimo,0)=0")

        con.commit()
        print("\nLimpieza ejecutada correctamente.")
        print("Reinicia Flask y revisa Productos / Proveedores / Recepciones.")
    except Exception as exc:
        con.rollback()
        print("\nERROR: limpieza cancelada, se ha hecho rollback.")
        print(str(exc))
        print(f"Puedes restaurar el backup si hiciera falta: {backup}")
        raise


if __name__ == "__main__":
    main()

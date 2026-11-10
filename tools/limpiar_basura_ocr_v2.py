#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Limpieza estructural OCR/IA Nexora Kitchen - v2.

No usa SQL fijo. Lee el esquema real de tu pepe4.db y usa:
- recepciones
- lineas_recepcion
- documentos_compra
- documento_compra_lineas_detectadas
si existen.

Uso:
    python tools/limpiar_basura_ocr_v2.py --analizar
    python tools/limpiar_basura_ocr_v2.py --ejecutar --modo-pruebas

Modo normal: no toca pedidos ni recepciones con pedido.
Modo pruebas: limpia recepciones directas sospechosas desde la fecha indicada.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


BASURA_TEXTO = [
    "PENDIENTE IA", "JUAN", "PAPA", "IIJ", "PDF", "OCR", "FACTURA SIMPLIFICADA",
    "TOTAL", "ENTREGADO", "CAMBIO", "TARJETA", "AUTORIZACION", "AUTORIZACIÓN",
    "OPERACION", "OPERACIÓN", "TERMINAL", "NUM.TRANSACC", "NIF", "NRO",
    "FECHA", "CAJERO", "DOCUMENTO", "CLIENTES VARIOS", "BASE IMP", "IGIC", "IVA",
]


def conectar(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def tabla_existe(cur: sqlite3.Cursor, tabla: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (tabla,),
    ).fetchone() is not None


def columnas(cur: sqlite3.Cursor, tabla: str) -> set[str]:
    if not tabla_existe(cur, tabla):
        return set()
    return {r["name"] for r in cur.execute(f"PRAGMA table_info({tabla})")}


def qmarks(vals) -> str:
    vals = list(vals)
    return ",".join("?" for _ in vals) if vals else "NULL"


def backup_db(db_path: str) -> str:
    origen = Path(db_path)
    carpeta = origen.parent / "backups"
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{origen.stem}_backup_antes_limpieza_estructural_{datetime.now().strftime('%Y%m%d_%H%M%S')}{origen.suffix}"
    shutil.copy2(origen, destino)
    return str(destino)


def norm(txt) -> str:
    return re.sub(r"\s+", " ", str(txt or "").strip())


def es_nombre_basura(txt) -> bool:
    n = norm(txt).upper()
    if not n:
        return True
    if any(b in n for b in BASURA_TEXTO):
        return True

    letras = sum(c.isalpha() for c in n)
    digitos = sum(c.isdigit() for c in n)
    raros = sum(not (c.isalnum() or c.isspace() or c in "ÑÁÉÍÓÚÜ.,-/+%()&") for c in n)

    if letras < 3:
        return True
    if raros >= 2:
        return True
    if digitos > letras and letras < 8:
        return True
    if len(n) <= 3:
        return True
    return False


def contar(cur, tabla, where="1=1", params=()):
    if not tabla_existe(cur, tabla):
        return 0
    return int(cur.execute(f"SELECT COUNT(*) FROM {tabla} WHERE {where}", params).fetchone()[0])


def ids_recepciones_sospechosas(cur, desde: str, modo_pruebas: bool) -> set[int]:
    if not tabla_existe(cur, "recepciones"):
        return set()

    cols = columnas(cur, "recepciones")
    cond = []
    params = []

    # En tu esquema no existe "tipo"; la recepción directa se identifica por pedido_id NULL.
    if "pedido_id" in cols:
        cond.append("r.pedido_id IS NULL")

    if "fecha_recepcion" in cols:
        cond.append("date(r.fecha_recepcion) >= date(?)")
        params.append(desde)

    # En modo normal solo limpiamos las claramente pendientes/no validadas.
    if not modo_pruebas:
        if "validada_operativamente" in cols:
            cond.append("COALESCE(r.validada_operativamente,0)=0")
        if "validada_economicamente" in cols:
            cond.append("COALESCE(r.validada_economicamente,0)=0")

    where = " AND ".join(cond) if cond else "1=1"

    sql = f"""
        SELECT r.id, r.proveedor_id, r.numero_albaran, r.observaciones, p.nombre AS proveedor
        FROM recepciones r
        LEFT JOIN proveedores p ON p.id = r.proveedor_id
        WHERE {where}
        ORDER BY r.id
    """

    res = set()
    for r in cur.execute(sql, tuple(params)).fetchall():
        proveedor = r["proveedor"] if "proveedor" in r.keys() else ""
        obs = r["observaciones"] if "observaciones" in r.keys() else ""
        alb = r["numero_albaran"] if "numero_albaran" in r.keys() else ""

        lineas = contar(cur, "lineas_recepcion", "recepcion_id=?", (r["id"],))
        lineas_invalidas = 0
        if tabla_existe(cur, "lineas_recepcion"):
            lr_cols = columnas(cur, "lineas_recepcion")
            checks = ["recepcion_id=?"]
            p2 = [r["id"]]
            if "producto_id" in lr_cols:
                checks.append("(producto_id IS NULL OR producto_id NOT IN (SELECT id FROM productos))")
            if "cantidad_recibida" in lr_cols:
                checks.append("COALESCE(cantidad_recibida,0)<=0")
            # Si todas las líneas tienen pinta rara, limpieza segura.
            lineas_invalidas = contar(cur, "lineas_recepcion", " AND ".join(checks), tuple(p2))

        sospechosa_por_nombre = es_nombre_basura(proveedor) or es_nombre_basura(obs) or es_nombre_basura(alb)
        sin_lineas = lineas == 0
        todas_invalidas = lineas > 0 and lineas_invalidas == lineas

        if modo_pruebas:
            # Para limpiar pruebas: directas recientes sospechosas o sin líneas útiles.
            if sospechosa_por_nombre or sin_lineas or todas_invalidas:
                res.add(int(r["id"]))
        else:
            # Modo conservador.
            if sin_lineas or todas_invalidas:
                res.add(int(r["id"]))

    return res


def ids_documentos_por_recepciones(cur, recep_ids: set[int]) -> set[int]:
    if not recep_ids or not tabla_existe(cur, "documentos_compra"):
        return set()

    cols = columnas(cur, "documentos_compra")
    res = set()
    ph = qmarks(recep_ids)

    if "recepcion_id" in cols:
        for r in cur.execute(f"SELECT id FROM documentos_compra WHERE recepcion_id IN ({ph})", tuple(recep_ids)):
            res.add(int(r["id"]))

    if tabla_existe(cur, "recepciones") and "documento_adjunto_id" in columnas(cur, "recepciones"):
        for r in cur.execute(f"SELECT documento_adjunto_id FROM recepciones WHERE id IN ({ph}) AND documento_adjunto_id IS NOT NULL", tuple(recep_ids)):
            res.add(int(r[0]))

    return res


def ids_documentos_ocr_sueltos(cur, desde: str, modo_pruebas: bool) -> set[int]:
    if not tabla_existe(cur, "documentos_compra"):
        return set()

    cols = columnas(cur, "documentos_compra")
    cond = []
    params = []

    if "procesado_ia" in cols:
        cond.append("COALESCE(procesado_ia,0)=1")
    if "fecha_documento" in cols:
        cond.append("date(fecha_documento) >= date(?)")
        params.append(desde)
    if "recepcion_id" in cols:
        cond.append("(recepcion_id IS NULL OR recepcion_id NOT IN (SELECT id FROM recepciones))")

    where = " AND ".join(cond) if cond else "1=0"
    return {int(r["id"]) for r in cur.execute(f"SELECT id FROM documentos_compra WHERE {where}", tuple(params)).fetchall()}


def productos_candidatos(cur, recep_ids: set[int], doc_ids: set[int], limpiar_sospechosos: bool) -> set[int]:
    res = set()

    if recep_ids and tabla_existe(cur, "lineas_recepcion") and "producto_id" in columnas(cur, "lineas_recepcion"):
        ph = qmarks(recep_ids)
        for r in cur.execute(f"SELECT DISTINCT producto_id FROM lineas_recepcion WHERE recepcion_id IN ({ph}) AND producto_id IS NOT NULL", tuple(recep_ids)):
            res.add(int(r["producto_id"]))

    if doc_ids and tabla_existe(cur, "documento_compra_lineas_detectadas") and "producto_id" in columnas(cur, "documento_compra_lineas_detectadas"):
        ph = qmarks(doc_ids)
        for r in cur.execute(f"""
            SELECT DISTINCT producto_id
            FROM documento_compra_lineas_detectadas
            WHERE documento_id IN ({ph}) AND producto_id IS NOT NULL
        """, tuple(doc_ids)):
            res.add(int(r["producto_id"]))

    if limpiar_sospechosos and tabla_existe(cur, "productos"):
        for r in cur.execute("SELECT id, nombre FROM productos"):
            if es_nombre_basura(r["nombre"]):
                res.add(int(r["id"]))

    return res


def producto_tiene_referencias_reales(cur, producto_id: int, recep_ids: set[int], doc_ids: set[int]) -> dict[str, int]:
    refs = {}

    tablas = [
        ("recetas_ingredientes", "producto_id"),
        ("lineas_menu", "producto_id"),
        ("lineas_menu_diario", "producto_id"),
        ("consumo_produccion", "producto_id"),
        ("consumo_lotes", "producto_id"),
        ("lineas_pedido", "producto_id"),
        ("pedidos_programados", "producto_id"),
        ("productos_proveedores", "producto_id"),
        ("producto_proveedor", "producto_id"),
        ("historico_precios", "producto_id"),
    ]

    for tabla, col in tablas:
        if tabla_existe(cur, tabla) and col in columnas(cur, tabla):
            n = contar(cur, tabla, f"{col}=?", (producto_id,))
            if n:
                refs[tabla] = n

    if tabla_existe(cur, "lineas_recepcion") and "producto_id" in columnas(cur, "lineas_recepcion"):
        if recep_ids:
            ph = qmarks(recep_ids)
            n = int(cur.execute(
                f"SELECT COUNT(*) FROM lineas_recepcion WHERE producto_id=? AND recepcion_id NOT IN ({ph})",
                (producto_id, *recep_ids),
            ).fetchone()[0])
        else:
            n = contar(cur, "lineas_recepcion", "producto_id=?", (producto_id,))
        if n:
            refs["lineas_recepcion_fuera_limpieza"] = n

    if tabla_existe(cur, "documento_compra_lineas_detectadas") and "producto_id" in columnas(cur, "documento_compra_lineas_detectadas"):
        if doc_ids:
            ph = qmarks(doc_ids)
            n = int(cur.execute(
                f"SELECT COUNT(*) FROM documento_compra_lineas_detectadas WHERE producto_id=? AND documento_id NOT IN ({ph})",
                (producto_id, *doc_ids),
            ).fetchone()[0])
        else:
            n = contar(cur, "documento_compra_lineas_detectadas", "producto_id=?", (producto_id,))
        if n:
            refs["lineas_ia_fuera_limpieza"] = n

    if tabla_existe(cur, "stock_almacen") and "producto_id" in columnas(cur, "stock_almacen"):
        n = contar(cur, "stock_almacen", "producto_id=? AND ABS(COALESCE(cantidad,0)) > 0.0001", (producto_id,))
        if n:
            refs["stock_actual"] = n

    if tabla_existe(cur, "lotes") and "producto_id" in columnas(cur, "lotes"):
        n = contar(cur, "lotes", "producto_id=? AND ABS(COALESCE(cantidad_actual,0)) > 0.0001", (producto_id,))
        if n:
            refs["lotes_con_stock"] = n

    return refs


def proveedores_candidatos(cur, recep_ids: set[int], limpiar_sospechosos: bool) -> set[int]:
    res = set()
    if recep_ids and tabla_existe(cur, "recepciones") and "proveedor_id" in columnas(cur, "recepciones"):
        ph = qmarks(recep_ids)
        for r in cur.execute(f"SELECT DISTINCT proveedor_id FROM recepciones WHERE id IN ({ph}) AND proveedor_id IS NOT NULL", tuple(recep_ids)):
            res.add(int(r["proveedor_id"]))

    if limpiar_sospechosos and tabla_existe(cur, "proveedores"):
        for r in cur.execute("SELECT id, nombre FROM proveedores"):
            if es_nombre_basura(r["nombre"]):
                res.add(int(r["id"]))

    return res


def proveedor_tiene_refs_reales(cur, proveedor_id: int, recep_ids: set[int], doc_ids: set[int]) -> dict[str, int]:
    refs = {}

    if tabla_existe(cur, "pedidos") and "proveedor_id" in columnas(cur, "pedidos"):
        n = contar(cur, "pedidos", "proveedor_id=?", (proveedor_id,))
        if n:
            refs["pedidos"] = n

    if tabla_existe(cur, "recepciones") and "proveedor_id" in columnas(cur, "recepciones"):
        if recep_ids:
            ph = qmarks(recep_ids)
            n = int(cur.execute(
                f"SELECT COUNT(*) FROM recepciones WHERE proveedor_id=? AND id NOT IN ({ph})",
                (proveedor_id, *recep_ids),
            ).fetchone()[0])
        else:
            n = contar(cur, "recepciones", "proveedor_id=?", (proveedor_id,))
        if n:
            refs["recepciones_fuera_limpieza"] = n

    for tabla in ["documentos_compra", "lotes", "historico_precios", "productos", "productos_proveedores"]:
        if tabla_existe(cur, tabla) and "proveedor_id" in columnas(cur, tabla):
            if tabla == "documentos_compra" and doc_ids:
                ph = qmarks(doc_ids)
                n = int(cur.execute(
                    f"SELECT COUNT(*) FROM documentos_compra WHERE proveedor_id=? AND id NOT IN ({ph})",
                    (proveedor_id, *doc_ids),
                ).fetchone()[0])
            else:
                n = contar(cur, tabla, "proveedor_id=?", (proveedor_id,))
            if n:
                refs[tabla] = n

    return refs


def borrar_ids(cur, tabla, col, ids):
    ids = list(ids)
    if not ids or not tabla_existe(cur, tabla) or col not in columnas(cur, tabla):
        return 0
    cur.execute(f"DELETE FROM {tabla} WHERE {col} IN ({qmarks(ids)})", tuple(ids))
    return max(cur.rowcount, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="pepe4.db")
    ap.add_argument("--desde", default="2026-04-24")
    ap.add_argument("--analizar", action="store_true")
    ap.add_argument("--ejecutar", action="store_true")
    ap.add_argument("--modo-pruebas", action="store_true", help="Limpia recepciones directas recientes sospechosas aunque estén OK. Usar para limpiar tus pruebas OCR.")
    ap.add_argument("--limpiar-productos-sospechosos", action="store_true")
    ap.add_argument("--limpiar-proveedores-sospechosos", action="store_true")
    args = ap.parse_args()

    db_path = args.db
    if not os.path.exists(db_path):
        alt = Path("instance") / args.db
        if alt.exists():
            db_path = str(alt)
        else:
            raise SystemExit(f"No encuentro la base: {args.db}")

    con = conectar(db_path)
    cur = con.cursor()

    recep_ids = ids_recepciones_sospechosas(cur, args.desde, args.modo_pruebas)
    doc_ids = ids_documentos_por_recepciones(cur, recep_ids) | ids_documentos_ocr_sueltos(cur, args.desde, args.modo_pruebas)

    prod_cand = productos_candidatos(cur, recep_ids, doc_ids, args.limpiar_productos_sospechosos)
    prod_borrar = set()
    prod_ret = {}
    for pid in sorted(prod_cand):
        refs = producto_tiene_referencias_reales(cur, pid, recep_ids, doc_ids)
        if refs:
            prod_ret[pid] = refs
        else:
            prod_borrar.add(pid)

    prov_cand = proveedores_candidatos(cur, recep_ids, args.limpiar_proveedores_sospechosos)
    prov_borrar = set()
    prov_ret = {}
    for pid in sorted(prov_cand):
        refs = proveedor_tiene_refs_reales(cur, pid, recep_ids, doc_ids)
        if refs:
            prov_ret[pid] = refs
        else:
            prov_borrar.add(pid)

    print("\n=== LIMPIEZA ESTRUCTURAL OCR / IA v2 ===")
    print(f"Base: {db_path}")
    print(f"Desde: {args.desde}")
    print(f"Modo pruebas: {args.modo_pruebas}")
    print("")
    print(f"Recepciones directas a limpiar: {len(recep_ids)} -> {sorted(recep_ids)}")
    print(f"Documentos OCR a limpiar:       {len(doc_ids)} -> {sorted(doc_ids)}")
    print(f"Productos borrables:            {len(prod_borrar)} -> {sorted(prod_borrar)}")
    print(f"Proveedores borrables:          {len(prov_borrar)} -> {sorted(prov_borrar)}")

    if prod_ret:
        print("\nProductos retenidos por referencias reales:")
        for pid, refs in prod_ret.items():
            nombre = ""
            if tabla_existe(cur, "productos"):
                row = cur.execute("SELECT nombre FROM productos WHERE id=?", (pid,)).fetchone()
                nombre = row["nombre"] if row else ""
            print(f"  - {pid} {nombre}: {refs}")

    if prov_ret:
        print("\nProveedores retenidos por referencias reales:")
        for pid, refs in prov_ret.items():
            nombre = ""
            if tabla_existe(cur, "proveedores"):
                row = cur.execute("SELECT nombre FROM proveedores WHERE id=?", (pid,)).fetchone()
                nombre = row["nombre"] if row else ""
            print(f"  - {pid} {nombre}: {refs}")

    if not args.ejecutar:
        print("\nANÁLISIS: no se ha borrado nada.")
        print("Para limpiar pruebas OCR recientes:")
        print("python tools/limpiar_basura_ocr_v2.py --ejecutar --modo-pruebas --limpiar-productos-sospechosos --limpiar-proveedores-sospechosos")
        return

    backup = backup_db(db_path)
    print(f"\nBackup creado: {backup}")

    try:
        con.execute("BEGIN")

        # Dependencias primero.
        borrar_ids(cur, "recepcion_validaciones", "recepcion_id", recep_ids)
        borrar_ids(cur, "lineas_recepcion", "recepcion_id", recep_ids)

        if doc_ids:
            borrar_ids(cur, "documento_compra_lineas_detectadas", "documento_id", doc_ids)
            borrar_ids(cur, "documentos_compra", "id", doc_ids)

        # Movimientos/lotes claramente generados por recepción directa OCR.
        if tabla_existe(cur, "movimientos_stock"):
            cols = columnas(cur, "movimientos_stock")
            if "motivo" in cols:
                cur.execute("""
                    DELETE FROM movimientos_stock
                    WHERE lower(COALESCE(motivo,'')) LIKE '%recepcion directa%'
                       OR lower(COALESCE(motivo,'')) LIKE '%recepción directa%'
                       OR lower(COALESCE(motivo,'')) LIKE '%ocr%'
                       OR lower(COALESCE(motivo,'')) LIKE '%ia%'
                """)

        if tabla_existe(cur, "lotes"):
            cols = columnas(cur, "lotes")
            if "documento_origen" in cols:
                cur.execute("""
                    DELETE FROM lotes
                    WHERE lower(COALESCE(documento_origen,'')) LIKE '%recepcion_directa%'
                       OR lower(COALESCE(documento_origen,'')) LIKE '%recepcion directa%'
                       OR lower(COALESCE(documento_origen,'')) LIKE '%recepción directa%'
                       OR lower(COALESCE(documento_origen,'')) LIKE '%ocr%'
                """)

        borrar_ids(cur, "recepciones", "id", recep_ids)

        # Tablas auxiliares de producto.
        for t in ["productos_proveedores", "producto_proveedor", "stock_almacen"]:
            borrar_ids(cur, t, "producto_id", prod_borrar)
        borrar_ids(cur, "productos", "id", prod_borrar)

        borrar_ids(cur, "proveedores", "id", prov_borrar)

        if tabla_existe(cur, "stock_almacen") and "cantidad" in columnas(cur, "stock_almacen"):
            extra = " AND COALESCE(stock_minimo,0)=0" if "stock_minimo" in columnas(cur, "stock_almacen") else ""
            cur.execute(f"DELETE FROM stock_almacen WHERE ABS(COALESCE(cantidad,0)) < 0.0001{extra}")

        con.commit()
        print("\nLimpieza v2 ejecutada correctamente.")
        print("Reinicia Flask y revisa Validar recepciones / Productos / Proveedores.")
    except Exception as exc:
        con.rollback()
        print("\nERROR: rollback ejecutado, no se ha dejado la base a medias.")
        print(exc)
        print(f"Backup: {backup}")
        raise


if __name__ == "__main__":
    main()

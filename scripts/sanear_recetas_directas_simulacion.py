# -*- coding: utf-8 -*-
"""Sanea recetas directas usadas por el simulador.

Muchas lineas de buffet son productos servidos tal cual: fruta, panecillos,
bolleria, encurtidos, etc. Si esas recetas no tienen ingredientes, la ERP no
puede calcular coste ni generar compras. Este script crea/enlaza un producto
base y deja trazabilidad de si los alergenos son inferidos o heredados.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sanear recetas directas de simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--limit", type=int, default=250)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_column_if_missing(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    if table_exists(conn, "recetas"):
        for definition in ("centro_id INTEGER", "alergenos_validado INTEGER DEFAULT 0", "observaciones TEXT"):
            add_column_if_missing(conn, "recetas", definition)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_saneamiento_recetas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receta_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            accion TEXT NOT NULL,
            alergenos_asignados TEXT,
            confianza TEXT,
            notas TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(receta_id, producto_id, accion)
        )
        """
    )


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().replace("/", " ").replace("-", " ").split())


def inferir_familia(nombre: str) -> str:
    n = norm(nombre)
    if any(x in n for x in ("naranja", "fresa", "melocoton", "melon", "sandia", "fruta")):
        return "fruta"
    if any(x in n for x in ("pepino", "remolacha", "cebollita", "lechuga", "tomate", "zanahoria", "verdura")):
        return "verdura"
    if any(x in n for x in ("pan", "croasant", "croissant", "napolitana", "ensaimada", "madalena", "magdalena", "cake", "cereales", "flakes")):
        return "panaderia_bolleria"
    if any(x in n for x in ("mahonesa", "mayonesa", "vinagreta")):
        return "salsas"
    if any(x in n for x in ("choped", "jamon", "pork", "salami", "fiambre")):
        return "charcuteria"
    return "otros"


def inferir_alergenos(nombre: str, familia: str) -> tuple[str, str, str]:
    n = norm(nombre)
    if familia in {"fruta", "verdura"}:
        return "Sin alergenos declarables", "media", "Inferido por producto vegetal directo; revisar contaminacion cruzada."
    if familia == "panaderia_bolleria":
        if "panecillo" in n or n.startswith("pan "):
            return "Gluten", "media", "Inferido por pan/bolleria; validar ficha del proveedor."
        return "Gluten, Huevo, Leche", "baja", "Inferido por bolleria; validar ficha del proveedor."
    if familia == "salsas":
        return "Huevo, Mostaza, Sulfitos", "baja", "Inferido por salsa tipo mahonesa/vinagreta; validar receta."
    if familia == "charcuteria":
        return "Sulfitos", "baja", "Inferido por fiambre/charcuteria; validar ficha del proveedor."
    return "revisar", "baja", "Sin inferencia fiable; requiere completar desde receta, proveedor o fuente externa."


def buscar_producto(conn: sqlite3.Connection, nombre: str, centro_id: int) -> sqlite3.Row | None:
    exact = norm(nombre)
    rows = conn.execute(
        """
        SELECT *
        FROM productos
        WHERE empresa_id=? AND COALESCE(activo,1)=1
          AND (centro_id=? OR centro_id IS NULL)
        """,
        (EMPRESA_ID, centro_id),
    ).fetchall()
    for row in rows:
        if norm(row["nombre"]) == exact:
            return row
    return None


def crear_producto(conn: sqlite3.Connection, receta: sqlite3.Row, centro_id: int, familia: str, alergenos: str, notas: str) -> int:
    nombre = receta["nombre"].strip()
    unidad = "kg" if familia in {"fruta", "verdura", "charcuteria"} else "ud"
    precio_base = {
        "fruta": 1.8,
        "verdura": 1.6,
        "panaderia_bolleria": 0.38,
        "salsas": 2.4,
        "charcuteria": 4.8,
        "otros": 1.0,
    }.get(familia, 1.0)
    cur = conn.execute(
        """
        INSERT INTO productos
        (nombre, cantidad, unidad, tipo_conservacion, familia, precio, stock_minimo,
         codigo, proveedor_id, observaciones, activo, empresa_id, centro_id,
         tipo_producto, visible_en_compras, visible_en_produccion, permite_compra_directa,
         alergenos, alergenos_validado)
        VALUES (?, 0, ?, ?, ?, ?, ?, ?, 6, ?, 1, ?, ?, 'materia_prima', 1, 1, 1, ?, 0)
        """,
        (
            nombre,
            unidad,
            "refrigerado" if familia in {"fruta", "verdura", "charcuteria"} else "seco",
            familia,
            precio_base,
            5 if unidad == "kg" else 24,
            f"SIM-REC-{receta['id']}",
            f"Producto creado por saneamiento de receta directa. {notas}",
            EMPRESA_ID,
            centro_id,
            alergenos,
        ),
    )
    return int(cur.lastrowid)


def recetas_objetivo(conn: sqlite3.Connection, centro_id: int, limit: int) -> list[sqlite3.Row]:
    union_menu = ""
    if table_exists(conn, "menu_platos"):
        union_menu = """
            UNION
            SELECT r.id
            FROM recetas r
            LEFT JOIN recetas_ingredientes ri ON ri.receta_id=r.id
            JOIN menu_platos mp ON mp.receta_id=r.id
            WHERE r.empresa_id=?
            GROUP BY r.id
            HAVING COUNT(ri.id)=0
        """
    sql = f"""
        SELECT r.*, 0 AS usos
        FROM recetas r
        JOIN (
            SELECT r.id
            FROM recetas r
            LEFT JOIN recetas_ingredientes ri ON ri.receta_id=r.id
            JOIN lineas_produccion lp ON lp.receta_id=r.id
            WHERE r.empresa_id=?
              AND ri.id IS NULL
            GROUP BY r.id
            {union_menu}
        ) objetivo ON objetivo.id=r.id
        ORDER BY r.id
        LIMIT ?
    """
    params = [EMPRESA_ID]
    if union_menu:
        params.append(EMPRESA_ID)
    params.append(int(limit))
    return conn.execute(sql, params).fetchall()


def recetas_operativas_sin_alergenos(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    menu_sql = ""
    if table_exists(conn, "menu_platos"):
        menu_sql = """
            UNION
            SELECT DISTINCT r.id
            FROM recetas r
            JOIN menu_platos mp ON mp.receta_id=r.id
            WHERE r.empresa_id=? AND TRIM(COALESCE(r.alergenos,''))=''
        """
    sql = f"""
        SELECT r.*
        FROM recetas r
        JOIN (
            SELECT DISTINCT r.id
            FROM recetas r
            JOIN lineas_produccion lp ON lp.receta_id=r.id
            WHERE r.empresa_id=? AND TRIM(COALESCE(r.alergenos,''))=''
            {menu_sql}
        ) objetivo ON objetivo.id=r.id
        ORDER BY r.id
        LIMIT ?
    """
    params = [EMPRESA_ID]
    if menu_sql:
        params.append(EMPRESA_ID)
    params.append(int(limit))
    return conn.execute(sql, params).fetchall()


def alergenos_desde_ingredientes(conn: sqlite3.Connection, receta_id: int) -> str:
    rows = conn.execute(
        """
        SELECT DISTINCT TRIM(COALESCE(p.alergenos,'')) AS alergenos
        FROM recetas_ingredientes ri
        JOIN productos p ON p.id=ri.producto_id
        WHERE ri.receta_id=?
          AND TRIM(COALESCE(p.alergenos,'')) <> ''
          AND LOWER(TRIM(COALESCE(p.alergenos,''))) NOT IN ('sin alergenos declarables', 'revisar')
        """,
        (receta_id,),
    ).fetchall()
    valores: list[str] = []
    for row in rows:
        for part in str(row["alergenos"] or "").replace(";", ",").split(","):
            item = part.strip()
            if item and item.lower() not in {v.lower() for v in valores}:
                valores.append(item)
    return ", ".join(valores)


def ejecutar(conn: sqlite3.Connection, args: argparse.Namespace) -> dict:
    ensure_schema(conn)
    creados = enlazados = alergenos_asignados = revisar = alergenos_desde_producto = 0
    muestras = []
    for receta in recetas_objetivo(conn, args.centro_id, args.limit):
        producto = buscar_producto(conn, receta["nombre"], args.centro_id)
        familia = inferir_familia(receta["nombre"])
        if producto:
            producto_id = int(producto["id"])
            alergenos = (producto["alergenos"] or "").strip()
            confianza = "alta" if alergenos else "media"
            notas = "Producto existente enlazado por nombre exacto."
            if not alergenos:
                alergenos, confianza, notas_extra = inferir_alergenos(receta["nombre"], familia)
                notas = f"{notas} {notas_extra}"
                conn.execute("UPDATE productos SET alergenos=COALESCE(NULLIF(alergenos,''), ?), alergenos_validado=0 WHERE id=?", (alergenos, producto_id))
        else:
            alergenos, confianza, notas = inferir_alergenos(receta["nombre"], familia)
            producto_id = crear_producto(conn, receta, args.centro_id, familia, alergenos, notas)
            creados += 1

        conn.execute(
            """
            INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad)
            VALUES (?, ?, 1)
            """,
            (int(receta["id"]), producto_id),
        )
        enlazados += 1
        if alergenos and not (receta["alergenos"] or "").strip():
            conn.execute(
                """
                UPDATE recetas
                SET alergenos=?, alergenos_validado=0,
                    observaciones=COALESCE(observaciones,'') || ?
                WHERE id=? AND empresa_id=?
                """,
                (alergenos, f"\n[Saneamiento simulador] {notas}", int(receta["id"]), EMPRESA_ID),
            )
            alergenos_asignados += 1
        if alergenos == "revisar":
            revisar += 1
        conn.execute(
            """
            INSERT OR IGNORE INTO simulador_saneamiento_recetas
            (receta_id, producto_id, accion, alergenos_asignados, confianza, notas, empresa_id, centro_id)
            VALUES (?, ?, 'producto_directo', ?, ?, ?, ?, ?)
            """,
            (int(receta["id"]), producto_id, alergenos, confianza, notas, EMPRESA_ID, args.centro_id),
        )
        if len(muestras) < 12:
            muestras.append({"receta": receta["nombre"], "producto_id": producto_id, "alergenos": alergenos, "confianza": confianza})
    for receta in recetas_operativas_sin_alergenos(conn, args.limit * 4):
        alergenos = alergenos_desde_ingredientes(conn, int(receta["id"]))
        notas = "Alergenos heredados de productos ingredientes."
        confianza = "media"
        if not alergenos:
            familia = inferir_familia(receta["nombre"])
            alergenos, confianza, notas = inferir_alergenos(receta["nombre"], familia)
        conn.execute(
            """
            UPDATE recetas
            SET alergenos=?, alergenos_validado=0,
                observaciones=COALESCE(observaciones,'') || ?
            WHERE id=? AND empresa_id=? AND TRIM(COALESCE(alergenos,''))=''
            """,
            (alergenos, f"\n[Saneamiento simulador] {notas}", int(receta["id"]), EMPRESA_ID),
        )
        alergenos_asignados += 1
        alergenos_desde_producto += 1 if "heredados" in notas else 0
        if alergenos == "revisar":
            revisar += 1
    conn.commit()
    return {
        "recetas_enlazadas": enlazados,
        "productos_creados": creados,
        "alergenos_asignados": alergenos_asignados,
        "alergenos_desde_productos": alergenos_desde_producto,
        "requieren_revision": revisar,
        "muestras": muestras,
    }


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    if not db.is_absolute():
        db = ROOT / db
    conn = connect(db)
    try:
        result = ejecutar(conn, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

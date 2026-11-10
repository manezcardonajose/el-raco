# -*- coding: utf-8 -*-
"""Marca fichas de alergenos de Albarracin como no calculables en coste.

Las fichas importadas de Albarracin contienen ingredientes y alergenos, pero
no cantidades. No deben alimentar coste, necesidades ni pedidos automaticos
hasta cargar un escandallo cuantitativo real.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
BACKUP_DIR = ROOT / "backups"
EMPRESA_ID = 2
CENTRO_ID = 8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reparar escandallos cualitativos Albarracin.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--sin-backup", action="store_true")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def safe_add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.strip().split()[0]
    if name not in columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def backup_db(path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = BACKUP_DIR / f"{path.stem}_pre_reparar_escandallos_albarracin_{stamp}{path.suffix}"
    shutil.copy2(path, out)
    return out


def main() -> None:
    args = parse_args()
    db = Path(args.db).resolve()
    if not db.exists():
        raise SystemExit(f"No existe la base: {db}")
    backup = None if args.sin_backup else backup_db(db)

    conn = connect(db)
    try:
        safe_add_column(conn, "recetas", "escandallo_fiable INTEGER DEFAULT 1")
        safe_add_column(conn, "recetas", "origen_escandallo TEXT")
        safe_add_column(conn, "recetas", "coste_validado INTEGER DEFAULT 0")

        recetas = conn.execute(
            """
            SELECT r.id
            FROM recetas r
            WHERE r.empresa_id=? AND r.centro_id=?
              AND COALESCE(r.coste_validado,0)=0
              AND NOT EXISTS (
                  SELECT 1
                  FROM recetas_ingredientes ri
                  WHERE ri.receta_id=r.id
                  GROUP BY ri.receta_id
                  HAVING SUM(CASE WHEN COALESCE(ri.cantidad,0) NOT IN (0,1) THEN 1 ELSE 0 END) > 0
              )
            """,
            (args.empresa_id, args.centro_id),
        ).fetchall()
        ids = [int(row["id"]) for row in recetas]

        if ids:
            placeholders = ",".join("?" for _ in ids)
            for receta_id in ids:
                row = conn.execute("SELECT descripcion FROM recetas WHERE id=?", (receta_id,)).fetchone()
                descripcion = str(row["descripcion"] or "").strip() if row else ""
                nota = "Coste bloqueado: ficha de alergenos sin cantidades."
                if nota not in descripcion:
                    descripcion = f"{descripcion} {nota}".strip()
                conn.execute(
                    """
                    UPDATE recetas
                    SET escandallo_fiable=0,
                        origen_escandallo='ficha_alergenos_sin_cantidades',
                        coste_validado=0,
                        descripcion=?
                    WHERE id=?
                    """,
                    (descripcion, receta_id),
                )
            conn.execute(
                f"""
                UPDATE recetas_ingredientes
                SET cantidad=0
                WHERE receta_id IN ({placeholders})
                  AND COALESCE(cantidad,0)=1
                """,
                ids,
            )

        fiables = conn.execute(
            """
            SELECT COUNT(*) FROM recetas
            WHERE empresa_id=? AND centro_id=? AND COALESCE(escandallo_fiable,1)=1
            """,
            (args.empresa_id, args.centro_id),
        ).fetchone()[0]
        bloqueadas = conn.execute(
            """
            SELECT COUNT(*) FROM recetas
            WHERE empresa_id=? AND centro_id=? AND COALESCE(escandallo_fiable,1)=0
            """,
            (args.empresa_id, args.centro_id),
        ).fetchone()[0]
        ingredientes_cero = conn.execute(
            """
            SELECT COUNT(*)
            FROM recetas_ingredientes ri
            JOIN recetas r ON r.id=ri.receta_id
            WHERE r.empresa_id=? AND r.centro_id=? AND COALESCE(ri.cantidad,0)=0
            """,
            (args.empresa_id, args.centro_id),
        ).fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    print(json.dumps({
        "ok": True,
        "backup": str(backup) if backup else None,
        "recetas_bloqueadas_en_esta_ejecucion": len(ids),
        "recetas_fiables": fiables,
        "recetas_bloqueadas_total": bloqueadas,
        "ingredientes_con_cantidad_cero": ingredientes_cero,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

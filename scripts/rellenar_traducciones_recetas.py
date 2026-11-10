import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "pepe4.db"
sys.path.insert(0, str(ROOT))

from helpers.receta_traducciones import autocompletar_traducciones


conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row
force = "--force" in sys.argv

where = """
    SELECT *
    FROM recetas
    WHERE COALESCE(TRIM(nombre), '') != ''
"""
if not force:
    where += """
      AND (
        COALESCE(TRIM(nombre_en), '') = ''
        OR COALESCE(TRIM(nombre_fr), '') = ''
        OR COALESCE(TRIM(nombre_de), '') = ''
        OR COALESCE(TRIM(nombre_it), '') = ''
      )
    """
rows = conn.execute(
    where + """
    ORDER BY id
    """
).fetchall()

actualizadas = 0
for receta in rows:
    traducciones = autocompletar_traducciones(receta["nombre"], {} if force else dict(receta))
    conn.execute(
        """
        UPDATE recetas
        SET nombre_en = ?,
            nombre_fr = ?,
            nombre_de = ?,
            nombre_it = ?
        WHERE id = ?
        """,
        (
            traducciones["nombre_en"],
            traducciones["nombre_fr"],
            traducciones["nombre_de"],
            traducciones["nombre_it"],
            receta["id"],
        ),
    )
    actualizadas += 1

conn.commit()
pendientes = conn.execute(
    """
    SELECT COUNT(*) AS total
    FROM recetas
    WHERE COALESCE(TRIM(nombre), '') != ''
      AND (
        COALESCE(TRIM(nombre_en), '') = ''
        OR COALESCE(TRIM(nombre_fr), '') = ''
        OR COALESCE(TRIM(nombre_de), '') = ''
        OR COALESCE(TRIM(nombre_it), '') = ''
      )
    """
).fetchone()["total"]
print({"recetas_revisadas": len(rows), "recetas_actualizadas": actualizadas, "pendientes": pendientes})
conn.close()

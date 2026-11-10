import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "pepe4.db"
EMPRESA_ID = 1
CENTRO_DESTINO_ID = 3

sys.path.insert(0, str(ROOT))

from blueprints.menus.routes import _copiar_ingredientes_receta


def norm_sql():
    return "LOWER(TRIM(nombre))"


conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row

recetas_vacias = conn.execute(
    """
    SELECT r.*
    FROM recetas r
    LEFT JOIN recetas_ingredientes ri ON ri.receta_id = r.id
    WHERE r.empresa_id = ?
      AND r.centro_id = ?
    GROUP BY r.id
    HAVING COUNT(ri.id) = 0
    ORDER BY r.id
    """,
    (EMPRESA_ID, CENTRO_DESTINO_ID),
).fetchall()

reparadas = 0
ingredientes_copiados = 0
productos_antes = conn.execute(
    "SELECT COUNT(*) AS total FROM productos WHERE empresa_id = ? AND centro_id = ?",
    (EMPRESA_ID, CENTRO_DESTINO_ID),
).fetchone()["total"]
sin_origen_con_ingredientes = []

for destino in recetas_vacias:
    origen = conn.execute(
        f"""
        SELECT r.*, COUNT(ri.id) AS ingredientes
        FROM recetas r
        LEFT JOIN recetas_ingredientes ri ON ri.receta_id = r.id
        WHERE r.empresa_id = ?
          AND COALESCE(r.centro_id, 0) != ?
          AND {norm_sql()} = LOWER(TRIM(?))
        GROUP BY r.id
        HAVING COUNT(ri.id) > 0
        ORDER BY COUNT(ri.id) DESC, r.id
        LIMIT 1
        """,
        (EMPRESA_ID, CENTRO_DESTINO_ID, destino["nombre"]),
    ).fetchone()
    if not origen:
        sin_origen_con_ingredientes.append(destino["nombre"])
        continue

    campos = {}
    for campo in ("descripcion", "alergenos", "imagen", "tiempo_preparacion", "dificultad", "categoria", "raciones_base", "nombre_en", "nombre_fr", "nombre_de", "nombre_it"):
        if campo in destino.keys() and campo in origen.keys():
            actual = destino[campo]
            nuevo = origen[campo]
            if (actual is None or str(actual).strip() == "") and nuevo not in (None, ""):
                campos[campo] = nuevo
    if campos:
        sets = ", ".join(f"{campo} = ?" for campo in campos)
        conn.execute(
            f"UPDATE recetas SET {sets} WHERE id = ? AND empresa_id = ?",
            list(campos.values()) + [destino["id"], EMPRESA_ID],
        )

    copiados = _copiar_ingredientes_receta(
        conn,
        EMPRESA_ID,
        origen["id"],
        destino["id"],
        CENTRO_DESTINO_ID,
    )
    if copiados:
        reparadas += 1
        ingredientes_copiados += copiados

conn.commit()

productos_despues = conn.execute(
    "SELECT COUNT(*) AS total FROM productos WHERE empresa_id = ? AND centro_id = ?",
    (EMPRESA_ID, CENTRO_DESTINO_ID),
).fetchone()["total"]
vacias_despues = conn.execute(
    """
    SELECT COUNT(*) AS total
    FROM (
        SELECT r.id
        FROM recetas r
        LEFT JOIN recetas_ingredientes ri ON ri.receta_id = r.id
        WHERE r.empresa_id = ?
          AND r.centro_id = ?
        GROUP BY r.id
        HAVING COUNT(ri.id) = 0
    )
    """,
    (EMPRESA_ID, CENTRO_DESTINO_ID),
).fetchone()["total"]

print(
    {
        "recetas_vacias_antes": len(recetas_vacias),
        "recetas_reparadas": reparadas,
        "ingredientes_copiados": ingredientes_copiados,
        "productos_creados": max(0, productos_despues - productos_antes),
        "recetas_vacias_despues": vacias_despues,
        "sin_origen_con_ingredientes": len(sin_origen_con_ingredientes),
        "muestra_sin_origen": sin_origen_con_ingredientes[:15],
    }
)
conn.close()

import sqlite3
from pathlib import Path
from collections import defaultdict


DB_PATH = Path(r"C:\Users\jmpep\Desktop\pepe4\pepe4.db")


def normalizar_nombre(nombre: str) -> str:
    return " ".join((nombre or "").strip().lower().split())


def tabla_existe(conn: sqlite3.Connection, nombre: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (nombre,)
    ).fetchone()
    return row is not None


def columna_existe(conn: sqlite3.Connection, tabla: str, columna: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
    return any(r[1] == columna for r in rows)


def contar_ingredientes(conn: sqlite3.Connection, receta_id: int) -> int:
    if not tabla_existe(conn, "recetas_ingredientes"):
        return 0
    row = conn.execute(
        "SELECT COUNT(*) FROM recetas_ingredientes WHERE receta_id = ?",
        (receta_id,)
    ).fetchone()
    return row[0] if row else 0


def longitud_texto(valor) -> int:
    return len((valor or "").strip())


def elegir_receta_principal(conn: sqlite3.Connection, recetas: list[sqlite3.Row]) -> sqlite3.Row:
    """
    Prioridad:
    1. Más ingredientes
    2. Más descripción
    3. Nombre en minúsculas exactas
    4. ID más pequeño
    """
    def score(r: sqlite3.Row):
        nombre = r["nombre"] or ""
        descripcion = r["descripcion"] if "descripcion" in r.keys() else ""
        num_ingredientes = contar_ingredientes(conn, r["id"])
        nombre_minus = 1 if nombre == nombre.lower() else 0

        return (
            num_ingredientes,
            longitud_texto(descripcion),
            nombre_minus,
            -r["id"],  # preferimos id más pequeño, luego invertimos en sort reverse
        )

    # Sort manual para mantener claridad
    recetas_ordenadas = sorted(
        recetas,
        key=lambda r: (
            contar_ingredientes(conn, r["id"]),
            longitud_texto(r["descripcion"] if "descripcion" in r.keys() else ""),
            1 if (r["nombre"] or "") == (r["nombre"] or "").lower() else 0,
            -r["id"],
        ),
        reverse=True
    )
    return recetas_ordenadas[0]


def actualizar_referencias(conn: sqlite3.Connection, receta_mala_id: int, receta_buena_id: int):
    """
    Reasigna todas las tablas que apunten a recetas.
    """
    posibles_referencias = [
        ("menu_platos", "receta_id"),
        ("lineas_menu", "receta_id"),
        ("lineas_produccion", "receta_id"),
    ]

    for tabla, columna in posibles_referencias:
        if tabla_existe(conn, tabla) and columna_existe(conn, tabla, columna):
            conn.execute(
                f"UPDATE {tabla} SET {columna} = ? WHERE {columna} = ?",
                (receta_buena_id, receta_mala_id)
            )


def fusionar_ingredientes(conn: sqlite3.Connection, receta_mala_id: int, receta_buena_id: int):
    """
    Mueve ingredientes si la receta buena no los tiene repetidos.
    """
    if not tabla_existe(conn, "recetas_ingredientes"):
        return

    rows = conn.execute("""
        SELECT *
        FROM recetas_ingredientes
        WHERE receta_id = ?
    """, (receta_mala_id,)).fetchall()

    for row in rows:
        producto_id = row["producto_id"] if "producto_id" in row.keys() else None
        if producto_id is None:
            continue

        existe = conn.execute("""
            SELECT id
            FROM recetas_ingredientes
            WHERE receta_id = ? AND producto_id = ?
            LIMIT 1
        """, (receta_buena_id, producto_id)).fetchone()

        if existe:
            # ya existe ingrediente en la buena, borramos el duplicado
            conn.execute("DELETE FROM recetas_ingredientes WHERE id = ?", (row["id"],))
        else:
            conn.execute(
                "UPDATE recetas_ingredientes SET receta_id = ? WHERE id = ?",
                (receta_buena_id, row["id"])
            )


def completar_datos_receta_buena(conn: sqlite3.Connection, receta_buena: sqlite3.Row, recetas_grupo: list[sqlite3.Row]):
    """
    Si la buena tiene campos vacíos, intenta completarlos desde las duplicadas.
    """
    if not tabla_existe(conn, "recetas"):
        return

    columnas = [r[1] for r in conn.execute("PRAGMA table_info(recetas)").fetchall()]
    actual = dict(receta_buena)

    campos_texto = [c for c in ["descripcion", "alergenos", "categoria"] if c in columnas]

    updates = {}
    for campo in campos_texto:
        actual_val = (actual.get(campo) or "").strip()
        if actual_val:
            continue

        for r in recetas_grupo:
            if r["id"] == receta_buena["id"]:
                continue
            candidato = (r[campo] or "").strip() if campo in r.keys() else ""
            if candidato:
                updates[campo] = candidato
                break

    if updates:
        sets = ", ".join([f"{k} = ?" for k in updates.keys()])
        vals = list(updates.values()) + [receta_buena["id"]]
        conn.execute(f"UPDATE recetas SET {sets} WHERE id = ?", vals)


def main():
    if not DB_PATH.exists():
        print(f"No existe la base de datos: {DB_PATH}")
        return

    backup_path = DB_PATH.with_name(DB_PATH.stem + "_backup_antes_deduplicar.db")
    if not backup_path.exists():
        backup_path.write_bytes(DB_PATH.read_bytes())
        print(f"Copia de seguridad creada: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    recetas = conn.execute("""
        SELECT *
        FROM recetas
        ORDER BY nombre, id
    """).fetchall()

    grupos = defaultdict(list)
    for r in recetas:
        clave = normalizar_nombre(r["nombre"])
        if clave:
            grupos[clave].append(r)

    total_grupos_duplicados = 0
    total_recetas_eliminadas = 0

    try:
        conn.execute("PRAGMA foreign_keys = OFF")

        for clave, grupo in grupos.items():
            if len(grupo) <= 1:
                continue

            total_grupos_duplicados += 1

            receta_buena = elegir_receta_principal(conn, grupo)
            completar_datos_receta_buena(conn, receta_buena, grupo)

            print(f"\nGrupo duplicado: {clave}")
            print(f"  -> se conserva ID {receta_buena['id']} | {receta_buena['nombre']}")

            for r in grupo:
                if r["id"] == receta_buena["id"]:
                    continue

                print(f"     elimina ID {r['id']} | {r['nombre']}")

                actualizar_referencias(conn, r["id"], receta_buena["id"])
                fusionar_ingredientes(conn, r["id"], receta_buena["id"])

                conn.execute("DELETE FROM recetas WHERE id = ?", (r["id"],))
                total_recetas_eliminadas += 1

        conn.commit()
        print("\nLIMPIEZA FINALIZADA")
        print(f"Grupos duplicados tratados: {total_grupos_duplicados}")
        print(f"Recetas eliminadas: {total_recetas_eliminadas}")

    except Exception as e:
        conn.rollback()
        print(f"\nERROR: {e}")
        print("Se ha hecho rollback. La base no se ha modificado.")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


if __name__ == "__main__":
    main()
import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_NOMBRE = "Hotel 1"


def parse_args():
    parser = argparse.ArgumentParser(description="Mover Animacion a departamento propio.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro", default=CENTRO_NOMBRE)
    return parser.parse_args()


def row(conn, query, params=()):
    return conn.execute(query, params).fetchone()


def ensure_dep(conn, empresa_id, centro_id):
    found = row(
        conn,
        """
        SELECT id FROM departamentos
        WHERE empresa_id=? AND centro_id=? AND lower(trim(nombre)) IN ('animacion', 'animación')
        ORDER BY activo DESC, id LIMIT 1
        """,
        (empresa_id, centro_id),
    )
    if found:
        conn.execute(
            """
            UPDATE departamentos
            SET nombre='ANIMACION', activo=1, tipo_operativo='animacion',
                descripcion=COALESCE(descripcion, 'Departamento operativo independiente.')
            WHERE id=? AND empresa_id=?
            """,
            (found["id"], empresa_id),
        )
        return found["id"], False
    cur = conn.execute(
        """
        INSERT INTO departamentos
        (nombre, descripcion, activo, empresa_id, tipo_operativo, centro_id)
        VALUES ('ANIMACION', 'Departamento operativo independiente de bares/restaurantes.', 1, ?, 'animacion', ?)
        """,
        (empresa_id, centro_id),
    )
    return cur.lastrowid, True


def main():
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        centro = row(
            conn,
            "SELECT id FROM centros_trabajo WHERE empresa_id=? AND lower(nombre)=lower(?) AND activo=1",
            (args.empresa_id, args.centro),
        )
        if not centro:
            raise SystemExit(f"No encuentro centro activo: {args.centro}")
        centro_id = centro["id"]
        dep_animacion, creado = ensure_dep(conn, args.empresa_id, centro_id)

        subdeps = conn.execute(
            """
            SELECT id FROM subdepartamentos
            WHERE empresa_id=? AND centro_id=? AND lower(trim(nombre)) IN ('animacion', 'animación')
            """,
            (args.empresa_id, centro_id),
        ).fetchall()
        subdep_ids = [r["id"] for r in subdeps]
        if not subdep_ids:
            cur = conn.execute(
                """
                INSERT INTO subdepartamentos (nombre, departamento_id, descripcion, activo, empresa_id, centro_id)
                VALUES ('Animacion', ?, 'Equipo de animacion y experiencia de cliente.', 1, ?, ?)
                """,
                (dep_animacion, args.empresa_id, centro_id),
            )
            subdep_ids = [cur.lastrowid]
        qmarks = ",".join("?" for _ in subdep_ids)
        params = [dep_animacion, *subdep_ids, args.empresa_id, centro_id]
        subdep_cambios = conn.execute(
            f"""
            UPDATE subdepartamentos
            SET departamento_id=?, activo=1,
                descripcion='Equipo de animacion y experiencia de cliente.'
            WHERE id IN ({qmarks}) AND empresa_id=? AND centro_id=?
            """,
            params,
        ).rowcount

        cambios = {"subdepartamentos": subdep_cambios}
        for table in ("puestos_operativos", "necesidades_operativas", "empleados"):
            if not row(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)):
                continue
            cambios[table] = conn.execute(
                f"""
                UPDATE {table}
                SET departamento_id=?
                WHERE empresa_id=? AND centro_id=? AND subdepartamento_id IN ({qmarks})
                """,
                [dep_animacion, args.empresa_id, centro_id, *subdep_ids],
            ).rowcount

        conn.commit()
        print({
            "centro_id": centro_id,
            "departamento_animacion_id": dep_animacion,
            "departamento_creado": creado,
            **cambios,
        })
    finally:
        conn.close()


if __name__ == "__main__":
    main()

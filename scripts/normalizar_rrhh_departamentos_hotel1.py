# -*- coding: utf-8 -*-
"""Normaliza empleados, puestos y necesidades RRHH de Hotel 1 por rol real.

No borra datos. Mueve cocina a COCINA CENTRAL, animacion a ANIMACION,
socorristas a PISCINA, y deja bares/restaurantes solo para sala, buffet y bar.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_NOMBRE = "Hotel 1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalizar RRHH Hotel 1 por departamento operativo.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro", default=CENTRO_NOMBRE)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def row(conn: sqlite3.Connection, sql: str, params=()):
    return conn.execute(sql, params).fetchone()


def ensure_dep(conn: sqlite3.Connection, empresa_id: int, centro_id: int, nombre: str, tipo: str) -> int:
    found = row(
        conn,
        """
        SELECT id FROM departamentos
        WHERE empresa_id=? AND centro_id=? AND lower(trim(nombre))=lower(trim(?))
        ORDER BY activo DESC, id LIMIT 1
        """,
        (empresa_id, centro_id, nombre),
    )
    if found:
        conn.execute(
            "UPDATE departamentos SET activo=1, tipo_operativo=COALESCE(NULLIF(tipo_operativo,''), ?) WHERE id=? AND empresa_id=?",
            (tipo, found["id"], empresa_id),
        )
        return int(found["id"])
    cur = conn.execute(
        """
        INSERT INTO departamentos (nombre, descripcion, activo, empresa_id, tipo_operativo, centro_id)
        VALUES (?, ?, 1, ?, ?, ?)
        """,
        (nombre, f"Departamento operativo {nombre}.", empresa_id, tipo, centro_id),
    )
    return int(cur.lastrowid)


def ensure_subdep(conn: sqlite3.Connection, empresa_id: int, centro_id: int, dep_id: int, nombre: str) -> int:
    found = row(
        conn,
        """
        SELECT id FROM subdepartamentos
        WHERE empresa_id=? AND centro_id=? AND departamento_id=? AND lower(trim(nombre))=lower(trim(?))
        ORDER BY activo DESC, id LIMIT 1
        """,
        (empresa_id, centro_id, dep_id, nombre),
    )
    if found:
        conn.execute("UPDATE subdepartamentos SET activo=1 WHERE id=? AND empresa_id=?", (found["id"], empresa_id))
        return int(found["id"])
    cur = conn.execute(
        """
        INSERT INTO subdepartamentos (nombre, departamento_id, descripcion, activo, empresa_id, centro_id)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (nombre, dep_id, f"Subdepartamento operativo {nombre}.", empresa_id, centro_id),
    )
    return int(cur.lastrowid)


def norm(text: str) -> str:
    return (text or "").strip().lower()


def destino_por_texto(texto: str, deps: dict[str, int], subs: dict[str, int]) -> tuple[int | None, int | None, str]:
    t = norm(texto)
    if any(x in t for x in ("desayun", "cocin", "partida", "cuarto frio", "bufetero", "plonge", "pinche", "tren de lavado")):
        if "desayun" in t:
            return deps["cocina"], subs["desayunos"], "cocina desayunos"
        if "frio" in t:
            return deps["cocina"], subs["frio"], "cocina cuarto frio"
        if any(x in t for x in ("plonge", "pinche", "tren de lavado")):
            return deps["cocina"], subs["plonge"], "cocina plonge"
        return deps["cocina"], subs["caliente"], "cocina caliente"
    if "animacion" in t or "animación" in t:
        return deps["animacion"], subs["animacion"], "animacion"
    if "socorrista" in t:
        return deps["piscina"], subs["piscina"], "piscina"
    if any(x in t for x in ("camarero buffet", "jefe de rango buffet", "jefe/a de rango buffet")):
        return deps["bares"], subs["buffet"], "sala buffet"
    if "camarero bar" in t or "bar piscina" in t:
        return deps["bares"], subs["bar"], "bar"
    return None, None, ""


def update_by_subdep(conn: sqlite3.Connection, empresa_id: int, centro_id: int, table: str, subdep_id: int, dep_id: int) -> int:
    if not row(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)):
        return 0
    return conn.execute(
        f"""
        UPDATE {table}
        SET departamento_id=?
        WHERE empresa_id=? AND centro_id=? AND subdepartamento_id=?
          AND COALESCE(departamento_id,0) != ?
        """,
        (dep_id, empresa_id, centro_id, subdep_id, dep_id),
    ).rowcount


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        centro = row(
            conn,
            "SELECT id FROM centros_trabajo WHERE empresa_id=? AND lower(nombre)=lower(?) AND activo=1",
            (args.empresa_id, args.centro),
        )
        if not centro:
            raise SystemExit(f"No encuentro centro activo: {args.centro}")
        centro_id = int(centro["id"])
        deps = {
            "cocina": ensure_dep(conn, args.empresa_id, centro_id, "COCINA CENTRAL", "cocina"),
            "bares": ensure_dep(conn, args.empresa_id, centro_id, "BARES/RESTAURANTES", "restaurante"),
            "animacion": ensure_dep(conn, args.empresa_id, centro_id, "ANIMACION", "animacion"),
            "piscina": ensure_dep(conn, args.empresa_id, centro_id, "PISCINA", "piscina"),
        }
        subs = {
            "desayunos": ensure_subdep(conn, args.empresa_id, centro_id, deps["cocina"], "Desayunos"),
            "frio": ensure_subdep(conn, args.empresa_id, centro_id, deps["cocina"], "Cuarto frio"),
            "caliente": ensure_subdep(conn, args.empresa_id, centro_id, deps["cocina"], "Cocina caliente"),
            "plonge": ensure_subdep(conn, args.empresa_id, centro_id, deps["cocina"], "Tren lavado y plonge"),
            "buffet": ensure_subdep(conn, args.empresa_id, centro_id, deps["bares"], "Buffet"),
            "bar": ensure_subdep(conn, args.empresa_id, centro_id, deps["bares"], "Bar piscina y sala de fiestas"),
            "animacion": ensure_subdep(conn, args.empresa_id, centro_id, deps["animacion"], "Animacion"),
            "piscina": ensure_subdep(conn, args.empresa_id, centro_id, deps["piscina"], "Piscina"),
        }

        # Reubica cualquier subdepartamento historico llamado exactamente Piscina.
        conn.execute(
            """
            UPDATE subdepartamentos
            SET departamento_id=?
            WHERE empresa_id=? AND centro_id=? AND lower(trim(nombre))='piscina'
              AND COALESCE(departamento_id,0) != ?
            """,
            (deps["piscina"], args.empresa_id, centro_id, deps["piscina"]),
        )
        piscina_subs = conn.execute(
            """
            SELECT id FROM subdepartamentos
            WHERE empresa_id=? AND centro_id=? AND departamento_id=? AND lower(trim(nombre))='piscina'
            ORDER BY id
            """,
            (args.empresa_id, centro_id, deps["piscina"]),
        ).fetchall()
        if len(piscina_subs) > 1:
            principal = int(piscina_subs[0]["id"])
            duplicados = [int(r["id"]) for r in piscina_subs[1:]]
            marks = ",".join("?" for _ in duplicados)
            for table in ("empleados", "puestos_operativos", "necesidades_operativas", "turnos_rrhh"):
                if row(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)):
                    conn.execute(
                        f"UPDATE {table} SET subdepartamento_id=? WHERE empresa_id=? AND centro_id=? AND subdepartamento_id IN ({marks})",
                        [principal, args.empresa_id, centro_id, *duplicados],
                    )
            conn.execute(
                f"UPDATE subdepartamentos SET activo=0, descripcion=COALESCE(descripcion,'') || ' | Duplicado consolidado en subdepartamento {principal}.' WHERE id IN ({marks})",
                duplicados,
            )
            subs["piscina"] = principal

        cambios = {"empleados": 0, "puestos_operativos": 0, "necesidades_operativas": 0, "turnos_rrhh": 0}
        for table, name_expr in [
            ("empleados", "COALESCE(rol,'') || ' ' || COALESCE((SELECT nombre FROM subdepartamentos WHERE id=subdepartamento_id),'')"),
            ("puestos_operativos", "COALESCE(nombre,'') || ' ' || COALESCE((SELECT nombre FROM subdepartamentos WHERE id=subdepartamento_id),'')"),
            ("necesidades_operativas", "COALESCE(etiqueta_operativa,'') || ' ' || COALESCE((SELECT nombre FROM puestos_operativos WHERE id=puesto_id),'') || ' ' || COALESCE((SELECT nombre FROM subdepartamentos WHERE id=subdepartamento_id),'')"),
            ("turnos_rrhh", "COALESCE(observaciones,'') || ' ' || COALESCE((SELECT nombre FROM puestos_operativos WHERE id=puesto_id),'') || ' ' || COALESCE((SELECT rol FROM empleados WHERE id=empleado_id),'')"),
        ]:
            if not row(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)):
                continue
            rows = conn.execute(
                f"SELECT id, {name_expr} AS texto FROM {table} WHERE empresa_id=? AND centro_id=?",
                (args.empresa_id, centro_id),
            ).fetchall()
            for item in rows:
                dep_id, sub_id, motivo = destino_por_texto(item["texto"], deps, subs)
                if not dep_id:
                    continue
                campos = "departamento_id=?, subdepartamento_id=?"
                params = [dep_id, sub_id, item["id"], args.empresa_id]
                if table == "empleados":
                    campos += ", motivo_cambio=?"
                    params = [dep_id, sub_id, f"Normalizacion RRHH Hotel 1: {motivo}", item["id"], args.empresa_id]
                cambios[table] += conn.execute(
                    f"UPDATE {table} SET {campos} WHERE id=? AND empresa_id=? AND (COALESCE(departamento_id,0) != ? OR COALESCE(subdepartamento_id,0) != ?)",
                    (*params, dep_id, sub_id),
                ).rowcount

        # Si un subdepartamento se movio, arrastra cualquier registro que dependa de el.
        for sub_name, dep_key in [("desayunos", "cocina"), ("frio", "cocina"), ("caliente", "cocina"), ("plonge", "cocina"), ("animacion", "animacion"), ("piscina", "piscina")]:
            for table in ("empleados", "puestos_operativos", "necesidades_operativas", "turnos_rrhh"):
                cambios[table] += update_by_subdep(conn, args.empresa_id, centro_id, table, subs[sub_name], deps[dep_key])

        conn.commit()
        print({"centro_id": centro_id, **cambios})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

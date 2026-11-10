# -*- coding: utf-8 -*-
"""Carga almacenes, equipos e inventario base de Hotel Albarracin."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
EMPRESA_ID = 2
CENTRO_ID = 8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sembrar inventario operativo Hotel Albarracin.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def add_column(conn: sqlite3.Connection, table: str, ddl: str) -> None:
    column = ddl.strip().split()[0]
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventario_existencias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            articulo_id INTEGER NOT NULL,
            cantidad REAL DEFAULT 0,
            estado TEXT DEFAULT 'operativo',
            ubicacion TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            almacen_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    add_column(conn, "inventario_existencias", "centro_id INTEGER")
    add_column(conn, "inventario_existencias", "punto_venta_id INTEGER")


def dep(conn: sqlite3.Connection, empresa_id: int, centro_id: int, nombre: str) -> int:
    row = conn.execute(
        """
        SELECT id FROM departamentos
        WHERE empresa_id=? AND centro_id=? AND UPPER(nombre)=UPPER(?) AND COALESCE(activo,1)=1
        """,
        (empresa_id, centro_id, nombre),
    ).fetchone()
    if not row:
        raise RuntimeError(f"No existe departamento {nombre}")
    return int(row["id"])


def pv(conn: sqlite3.Connection, empresa_id: int, centro_id: int, nombre: str) -> int | None:
    row = conn.execute(
        """
        SELECT id FROM puntos_venta
        WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER(?) AND COALESCE(activo,1)=1
        """,
        (empresa_id, centro_id, nombre),
    ).fetchone()
    return int(row["id"]) if row else None


def upsert_almacen(conn: sqlite3.Connection, empresa_id: int, centro_id: int, data: dict) -> int:
    row = conn.execute(
        """
        SELECT id FROM almacenes
        WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER(?)
        """,
        (empresa_id, centro_id, data["nombre"]),
    ).fetchone()
    values = (
        data["nombre"], data["tipo"], data["ubicacion"], data["responsable"],
        data["departamento_id"], data.get("requiere_control_temperatura", 0),
        data.get("rango_min"), data.get("rango_max"),
    )
    if row:
        almacen_id = int(row["id"])
        conn.execute(
            """
            UPDATE almacenes
            SET nombre=?, tipo=?, ubicacion=?, responsable=?, departamento_id=?,
                requiere_control_temperatura=?, rango_min=?, rango_max=?, activo=1
            WHERE id=?
            """,
            (*values, almacen_id),
        )
        return almacen_id
    cur = conn.execute(
        """
        INSERT INTO almacenes
        (nombre, tipo, ubicacion, responsable, departamento_id,
         requiere_control_temperatura, rango_min, rango_max, activo, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (*values, empresa_id, centro_id),
    )
    return int(cur.lastrowid)


def upsert_equipo_mantenimiento(conn: sqlite3.Connection, empresa_id: int, centro_id: int, data: dict) -> int:
    row = conn.execute(
        """
        SELECT id FROM equipos_mantenimiento
        WHERE empresa_id=? AND centro_id=? AND codigo=?
        """,
        (empresa_id, centro_id, data["codigo"]),
    ).fetchone()
    values = (
        data["nombre"], data["tipo"], data.get("marca"), data.get("modelo"),
        data["ubicacion"], data["departamento_id"], data.get("punto_venta_id"),
        data["codigo"], data.get("familia"), data.get("categoria_operativa"),
        data.get("clase_activo", "equipo"), data.get("criticidad", "media"),
        data.get("requiere_appcc", 0), data.get("almacen_id"),
    )
    if row:
        equipo_id = int(row["id"])
        conn.execute(
            """
            UPDATE equipos_mantenimiento
            SET nombre=?, tipo=?, marca=?, modelo=?, ubicacion=?, departamento_id=?,
                punto_venta_id=?, codigo=?, familia=?, categoria_operativa=?,
                clase_activo=?, criticidad=?, requiere_appcc=?, almacen_id=?, activo=1
            WHERE id=?
            """,
            (*values, equipo_id),
        )
        return equipo_id
    cur = conn.execute(
        """
        INSERT INTO equipos_mantenimiento
        (nombre, tipo, marca, modelo, ubicacion, departamento_id, punto_venta_id,
         codigo, familia, categoria_operativa, clase_activo, criticidad, requiere_appcc,
         almacen_id, activo, empresa_id, centro_id, origen_modulo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'seed_albarracin')
        """,
        (*values, empresa_id, centro_id),
    )
    return int(cur.lastrowid)


def upsert_equipo_appcc(conn: sqlite3.Connection, empresa_id: int, centro_id: int, data: dict, equipo_mantenimiento_id: int | None = None) -> int:
    row = conn.execute(
        """
        SELECT id FROM equipos
        WHERE empresa_id=? AND centro_id=? AND codigo=?
        """,
        (empresa_id, centro_id, data["codigo"]),
    ).fetchone()
    values = (
        data["nombre"], data["tipo"], data["ubicacion"], data.get("rango_min"),
        data.get("rango_max"), data.get("responsable"), data["departamento_id"],
        data.get("punto_venta_id"), data["codigo"], equipo_mantenimiento_id,
        data.get("almacen_id"), data.get("requiere_mantenimiento", 1),
    )
    if row:
        equipo_id = int(row["id"])
        conn.execute(
            """
            UPDATE equipos
            SET nombre=?, tipo=?, ubicacion=?, rango_min=?, rango_max=?, responsable=?,
                departamento_id=?, punto_venta_id=?, codigo=?, equipo_mantenimiento_id=?,
                almacen_id=?, requiere_mantenimiento=?, activo=1
            WHERE id=?
            """,
            (*values, equipo_id),
        )
        return equipo_id
    cur = conn.execute(
        """
        INSERT INTO equipos
        (nombre, tipo, ubicacion, rango_min, rango_max, responsable,
         departamento_id, punto_venta_id, codigo, equipo_mantenimiento_id,
         almacen_id, requiere_mantenimiento, activo, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (*values, empresa_id, centro_id),
    )
    return int(cur.lastrowid)


def upsert_articulo(conn: sqlite3.Connection, empresa_id: int, centro_id: int, data: dict) -> int:
    row = conn.execute(
        """
        SELECT id FROM inventario_articulos
        WHERE empresa_id=? AND centro_id=? AND codigo=?
        """,
        (empresa_id, centro_id, data["codigo"]),
    ).fetchone()
    values = (
        data["codigo"], data["nombre"], data["familia"], data.get("categoria"),
        data.get("unidad", "ud"), data.get("descripcion", data["nombre"]),
        data.get("stock_minimo", 0), data.get("valor_unitario", 0),
        data.get("uso_operativo", "otros"), data.get("material"),
        data.get("formato"), data.get("capacidad"),
        data.get("compatible_appcc", 1), data.get("apto_caliente", 0),
        data.get("apto_frio", 0), data.get("apto_horno", 0),
        data.get("apto_lavavajillas", 1), data.get("rompible", 0),
        data.get("requiere_reposicion_buffet", 0),
    )
    if row:
        articulo_id = int(row["id"])
        conn.execute(
            """
            UPDATE inventario_articulos
            SET nombre=?, familia=?, categoria=?, unidad=?, descripcion=?, stock_minimo=?,
                valor_unitario=?, uso_operativo=?, material=?, formato=?, capacidad=?,
                compatible_appcc=?, apto_caliente=?, apto_frio=?, apto_horno=?,
                apto_lavavajillas=?, rompible=?, requiere_reposicion_buffet=?, activo=1
            WHERE id=?
            """,
            (values[1], *values[2:], articulo_id),
        )
        return articulo_id
    cur = conn.execute(
        """
        INSERT INTO inventario_articulos
        (codigo, nombre, familia, categoria, unidad, descripcion, stock_minimo,
         valor_unitario, uso_operativo, material, formato, capacidad,
         compatible_appcc, apto_caliente, apto_frio, apto_horno, apto_lavavajillas,
         rompible, requiere_reposicion_buffet, activo, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (*values, empresa_id, centro_id),
    )
    return int(cur.lastrowid)


def set_existencia(conn: sqlite3.Connection, empresa_id: int, centro_id: int, articulo_id: int, data: dict) -> None:
    row = conn.execute(
        """
        SELECT id FROM inventario_existencias
        WHERE articulo_id=? AND empresa_id=? AND centro_id=?
          AND COALESCE(ubicacion,'')=COALESCE(?,'')
          AND COALESCE(almacen_id,0)=COALESCE(?,0)
          AND COALESCE(punto_venta_id,0)=COALESCE(?,0)
        """,
        (articulo_id, empresa_id, centro_id, data.get("ubicacion"), data.get("almacen_id"), data.get("punto_venta_id")),
    ).fetchone()
    values = (
        data["cantidad"], data.get("ubicacion"), data.get("departamento_id"),
        data.get("punto_venta_id"), data.get("almacen_id"),
    )
    if row:
        conn.execute(
            """
            UPDATE inventario_existencias
            SET cantidad=?, ubicacion=?, departamento_id=?, punto_venta_id=?,
                almacen_id=?, estado='operativo', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (*values, row["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO inventario_existencias
            (articulo_id, cantidad, estado, ubicacion, departamento_id, punto_venta_id,
             almacen_id, empresa_id, centro_id, updated_at)
            VALUES (?, ?, 'operativo', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (articulo_id, *values, empresa_id, centro_id),
        )


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    empresa_id = args.empresa_id
    centro_id = args.centro_id
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        deps = {
            "admin": dep(conn, empresa_id, centro_id, "ADMINISTRACION"),
            "recepcion": dep(conn, empresa_id, centro_id, "RECEPCION"),
            "sala": dep(conn, empresa_id, centro_id, "SALA / RESTAURANTE"),
            "pisos": dep(conn, empresa_id, centro_id, "PISOS"),
            "cocina": dep(conn, empresa_id, centro_id, "COCINA"),
            "mantenimiento": dep(conn, empresa_id, centro_id, "MANTENIMIENTO"),
        }
        pvs = {"cafeteria": pv(conn, empresa_id, centro_id, "Cafetería"), "restaurante": pv(conn, empresa_id, centro_id, "Restaurante")}

        almacenes = {}
        for data in [
            {"key": "sala", "nombre": "Almacén sala/restaurante", "tipo": "seco", "ubicacion": "Sala/restaurante", "responsable": "Sala / Restaurante", "departamento_id": deps["sala"]},
            {"key": "pisos", "nombre": "Almacén pisos", "tipo": "seco", "ubicacion": "Office pisos", "responsable": "Pisos", "departamento_id": deps["pisos"]},
            {"key": "frio", "nombre": "Cámara de frío cocina", "tipo": "refrigerado", "ubicacion": "Cocina", "responsable": "Cocina", "departamento_id": deps["cocina"], "requiere_control_temperatura": 1, "rango_min": 0, "rango_max": 4},
            {"key": "congelado", "nombre": "Arcones congelado cocina", "tipo": "congelado", "ubicacion": "Cocina - zona arcones", "responsable": "Cocina", "departamento_id": deps["cocina"], "requiere_control_temperatura": 1, "rango_min": -22, "rango_max": -18},
            {"key": "seco", "nombre": "Pequeño almacén seco cocina", "tipo": "seco", "ubicacion": "Cocina", "responsable": "Cocina", "departamento_id": deps["cocina"]},
            {"key": "mantenimiento", "nombre": "Almacén mantenimiento habitación sin reformar", "tipo": "seco", "ubicacion": "Habitación sin reformar", "responsable": "Mantenimiento", "departamento_id": deps["mantenimiento"]},
        ]:
            almacenes[data["key"]] = upsert_almacen(conn, empresa_id, centro_id, data)

        equipos = []
        def add_equipo(codigo, nombre, tipo, dep_key, ubicacion, cantidad=1, pv_key=None, almacen_key=None, appcc=False, rango=None, criticidad="media"):
            created = []
            for i in range(1, cantidad + 1):
                suffix = f"-{i:02d}" if cantidad > 1 else ""
                data = {
                    "codigo": f"ALB-{codigo}{suffix}",
                    "nombre": f"{nombre} {i}" if cantidad > 1 else nombre,
                    "tipo": tipo,
                    "ubicacion": ubicacion,
                    "departamento_id": deps[dep_key],
                    "punto_venta_id": pvs.get(pv_key) if pv_key else None,
                    "almacen_id": almacenes.get(almacen_key) if almacen_key else None,
                    "familia": tipo,
                    "categoria_operativa": "frio" if appcc else "operativo",
                    "criticidad": criticidad,
                    "requiere_appcc": 1 if appcc else 0,
                }
                equipo_id = upsert_equipo_mantenimiento(conn, empresa_id, centro_id, data)
                if appcc:
                    appcc_data = {
                        "codigo": data["codigo"],
                        "nombre": data["nombre"],
                        "tipo": tipo,
                        "ubicacion": ubicacion,
                        "rango_min": rango[0] if rango else None,
                        "rango_max": rango[1] if rango else None,
                        "responsable": data["nombre"],
                        "departamento_id": deps[dep_key],
                        "punto_venta_id": data["punto_venta_id"],
                        "almacen_id": data["almacen_id"],
                    }
                    upsert_equipo_appcc(conn, empresa_id, centro_id, appcc_data, equipo_id)
                created.append(equipo_id)
            equipos.extend(created)

        add_equipo("BAR-CAF", "Cafetera cafetería", "cafetera", "sala", "Cafetería", pv_key="cafeteria")
        add_equipo("BAR-VINO", "Vitrina de vinos cafetería", "vitrina_vinos", "sala", "Cafetería", pv_key="cafeteria", appcc=True, rango=(6, 14))
        add_equipo("BAR-ZUM", "Zumera cafetería", "zumera", "sala", "Cafetería", pv_key="cafeteria")
        add_equipo("BAR-BOT", "Botellero cafetería", "botellero", "sala", "Cafetería", cantidad=2, pv_key="cafeteria", appcc=True, rango=(0, 6))
        add_equipo("RES-CAF", "Cafetera restaurante", "cafetera", "sala", "Restaurante", pv_key="restaurante")
        add_equipo("RES-CONG-VIT", "Vitrina congelador restaurante", "vitrina_congelador", "sala", "Restaurante", cantidad=2, pv_key="restaurante", appcc=True, rango=(-22, -18), criticidad="alta")
        add_equipo("RES-BOT", "Botellero restaurante", "botellero", "sala", "Restaurante", cantidad=3, pv_key="restaurante", appcc=True, rango=(0, 6))
        add_equipo("RES-LAV-CAP", "Lavavajillas de capota", "lavavajillas_capota", "sala", "Restaurante", pv_key="restaurante", criticidad="alta")
        add_equipo("BUFF-FRIO", "Máquina de frío buffet", "buffet_frio", "sala", "Buffet restaurante", cantidad=2, pv_key="restaurante", appcc=True, rango=(0, 4), criticidad="alta")
        add_equipo("BUFF-CAL", "Máquina caliente gastronorm 4 huecos", "buffet_caliente", "sala", "Buffet restaurante", pv_key="restaurante", appcc=True, rango=(65, 90), criticidad="alta")
        add_equipo("BUFF-CAFE", "Máquina de café buffet", "maquina_cafe_buffet", "sala", "Buffet restaurante", cantidad=2, pv_key="restaurante")
        add_equipo("BUFF-ZUMO", "Máquina de zumos buffet", "maquina_zumos_buffet", "sala", "Buffet restaurante", cantidad=2, pv_key="restaurante")
        add_equipo("COC-CAM-FRIO", "Cámara de frío cocina", "camara_refrigerada", "cocina", "Cocina", almacen_key="frio", appcc=True, rango=(0, 4), criticidad="alta")
        add_equipo("COC-ARCON", "Arcón congelador cocina", "arcon_congelador", "cocina", "Cocina - zona arcones", cantidad=12, almacen_key="congelado", appcc=True, rango=(-22, -18), criticidad="alta")
        add_equipo("TPV-CAF", "Terminal TPV cafetería", "terminal_tpv", "sala", "Cafetería", pv_key="cafeteria", criticidad="alta")
        add_equipo("TPV-RES", "Terminal TPV restaurante", "terminal_tpv", "sala", "Restaurante", pv_key="restaurante", criticidad="alta")
        add_equipo("PC-REC", "Ordenador recepción", "ordenador", "recepcion", "Recepción", cantidad=2, criticidad="alta")
        add_equipo("PC-DIR", "Ordenador dirección/administración", "ordenador", "admin", "Dirección/Administración", criticidad="media")

        items = [
            {"codigo": "ALB-MESA-SEN-4P", "nombre": "Mesa cuadrada 4 personas antigua/señorial", "familia": "mobiliario", "categoria": "sala", "cantidad": 18, "ubicacion": "Restaurante", "departamento_id": deps["sala"], "punto_venta_id": pvs["restaurante"], "material": "madera", "capacidad": "4 personas", "valor_unitario": 120},
            {"codigo": "ALB-SILLA-SEN", "nombre": "Silla comedor estilo antiguo/señorial", "familia": "mobiliario", "categoria": "sala", "cantidad": 72, "ubicacion": "Restaurante", "departamento_id": deps["sala"], "punto_venta_id": pvs["restaurante"], "material": "madera/tapizado", "valor_unitario": 55},
            {"codigo": "ALB-MESA-CAF", "nombre": "Mesa cafetería", "familia": "mobiliario", "categoria": "cafeteria", "cantidad": 8, "ubicacion": "Cafetería", "departamento_id": deps["sala"], "punto_venta_id": pvs["cafeteria"], "material": "madera", "capacidad": "2-4 personas", "valor_unitario": 90},
            {"codigo": "ALB-SILLA-CAF", "nombre": "Silla cafetería", "familia": "mobiliario", "categoria": "cafeteria", "cantidad": 32, "ubicacion": "Cafetería", "departamento_id": deps["sala"], "punto_venta_id": pvs["cafeteria"], "material": "madera/tapizado", "valor_unitario": 45},
            {"codigo": "ALB-HAB-CAMA", "nombre": "Cama habitación base estimada", "familia": "habitacion", "categoria": "habitacion", "cantidad": 85, "ubicacion": "Habitaciones", "departamento_id": deps["pisos"], "material": "mobiliario", "descripcion": "Estimado por capacidad/tipos; ajustar con inventario real.", "valor_unitario": 280},
            {"codigo": "ALB-HAB-MESILLA", "nombre": "Mesilla habitación base estimada", "familia": "habitacion", "categoria": "habitacion", "cantidad": 82, "ubicacion": "Habitaciones", "departamento_id": deps["pisos"], "material": "madera", "descripcion": "Inventario estimado por foto/web.", "valor_unitario": 60},
            {"codigo": "ALB-HAB-ARMARIO", "nombre": "Armario habitación base estimado", "familia": "habitacion", "categoria": "habitacion", "cantidad": 41, "ubicacion": "Habitaciones", "departamento_id": deps["pisos"], "material": "madera", "descripcion": "Inventario estimado por foto/web.", "valor_unitario": 220},
            {"codigo": "ALB-HAB-ESCRITORIO", "nombre": "Mesa escritorio habitación base estimada", "familia": "habitacion", "categoria": "habitacion", "cantidad": 41, "ubicacion": "Habitaciones", "departamento_id": deps["pisos"], "material": "madera", "descripcion": "Inventario estimado por foto/web.", "valor_unitario": 95},
            {"codigo": "ALB-HAB-SILLA", "nombre": "Silla habitación base estimada", "familia": "habitacion", "categoria": "habitacion", "cantidad": 41, "ubicacion": "Habitaciones", "departamento_id": deps["pisos"], "material": "madera/tapizado", "descripcion": "Inventario estimado por foto/web.", "valor_unitario": 45},
            {"codigo": "ALB-HAB-TV", "nombre": "Televisor habitación base estimado", "familia": "habitacion", "categoria": "electronica", "cantidad": 41, "ubicacion": "Habitaciones", "departamento_id": deps["pisos"], "descripcion": "Inventario estimado por foto/web.", "valor_unitario": 180},
            {"codigo": "ALB-HAB-CORTINAS", "nombre": "Juego cortinas habitación estimado", "familia": "habitacion", "categoria": "textil", "cantidad": 41, "ubicacion": "Habitaciones", "departamento_id": deps["pisos"], "material": "textil", "descripcion": "Inventario estimado por foto/web.", "valor_unitario": 70},
        ]
        articulos = 0
        for data in items:
            articulo_id = upsert_articulo(conn, empresa_id, centro_id, data)
            set_existencia(conn, empresa_id, centro_id, articulo_id, data)
            articulos += 1

        conn.commit()
        resumen = {
            "empresa_id": empresa_id,
            "centro_id": centro_id,
            "almacenes": len(almacenes),
            "equipos_mantenimiento": len(equipos),
            "inventario_articulos": articulos,
        }
        print(resumen)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

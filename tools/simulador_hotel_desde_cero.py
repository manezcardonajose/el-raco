# -*- coding: utf-8 -*-
"""
Simulador integral de El Raco ERP.

Crea una base SQLite separada para probar la ERP desde cero sin tocar pepe4.db:
hotel, usuarios, habitaciones, reservas, productos, stock, compras, produccion,
mantenimiento, TPV y contabilidad.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


ROOT = Path(__file__).resolve().parents[1]
REAL_DB = (ROOT / "pepe4.db").resolve()
DEFAULT_DB = ROOT / "simulacion_el_raco.db"
EMPRESA_ID = 1
ADMIN_EMAIL = "admin@elraco.local"
ADMIN_PASSWORD = "admin"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crea una demo limpia de El Raco ERP.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Ruta de la base demo.")
    parser.add_argument("--reset", action="store_true", help="Borra la base demo antes de crearla.")
    parser.add_argument("--habitaciones", type=int, default=80, help="Habitaciones a crear.")
    parser.add_argument("--dias", type=int, default=45, help="Dias de simulacion.")
    parser.add_argument("--ocupacion", type=float, default=0.72, help="Ocupacion aproximada 0-1.")
    parser.add_argument("--seed", type=int, default=20260512, help="Semilla aleatoria.")
    return parser.parse_args()


def safe_db_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if path == REAL_DB:
        raise RuntimeError("Proteccion activa: el simulador no puede usar pepe4.db.")
    if path.suffix.lower() != ".db":
        raise RuntimeError("La base de simulacion debe terminar en .db.")
    return path


def reset_db(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"), Path(str(path) + "-journal")):
        if candidate.exists():
            candidate.unlink()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def filter_values(conn: sqlite3.Connection, table: str, values: dict) -> dict:
    valid = columns(conn, table)
    return {key: value for key, value in values.items() if key in valid}


def insert_row(conn: sqlite3.Connection, table: str, values: dict) -> int:
    values = filter_values(conn, table, values)
    keys = list(values.keys())
    placeholders = ", ".join(["?"] * len(keys))
    sql = f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})"
    cur = conn.execute(sql, [values[key] for key in keys])
    return int(cur.lastrowid)


def upsert_by_keys(conn: sqlite3.Connection, table: str, key_cols: list[str], values: dict) -> int:
    values = filter_values(conn, table, values)
    key_cols = [key for key in key_cols if key in values]
    where = " AND ".join([f"{key}=?" for key in key_cols])
    params = [values[key] for key in key_cols]
    row = conn.execute(f"SELECT id FROM {table} WHERE {where} LIMIT 1", params).fetchone()
    if row:
        update_cols = [key for key in values.keys() if key not in key_cols and key != "id"]
        if update_cols:
            set_sql = ", ".join([f"{key}=?" for key in update_cols])
            conn.execute(
                f"UPDATE {table} SET {set_sql} WHERE id=?",
                [values[key] for key in update_cols] + [row["id"]],
            )
        return int(row["id"])
    return insert_row(conn, table, values)


def id_by_name(conn: sqlite3.Connection, table: str, name: str, empresa_id: int | None = EMPRESA_ID) -> int | None:
    if not table_exists(conn, table):
        return None
    sql = "SELECT id FROM {table} WHERE LOWER(nombre)=LOWER(?)".format(table=table)
    params: list = [name]
    if empresa_id is not None and "empresa_id" in columns(conn, table):
        sql += " AND (empresa_id=? OR empresa_id IS NULL)"
        params.append(empresa_id)
    sql += " ORDER BY id LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    return int(row["id"]) if row else None


def id_by_codigo(conn: sqlite3.Connection, table: str, codigo: str, empresa_id: int | None = EMPRESA_ID) -> int | None:
    if not table_exists(conn, table) or "codigo" not in columns(conn, table):
        return None
    sql = f"SELECT id FROM {table} WHERE LOWER(codigo)=LOWER(?)"
    params: list = [codigo]
    if empresa_id is not None and "empresa_id" in columns(conn, table):
        sql += " AND (empresa_id=? OR empresa_id IS NULL)"
        params.append(empresa_id)
    sql += " ORDER BY id LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    return int(row["id"]) if row else None


def dinero(value: float) -> float:
    return round(float(value or 0), 2)


def warmup_schema(db_path: Path) -> list[dict]:
    os.environ["ERP_DATABASE"] = str(db_path)
    sys.path.insert(0, str(ROOT))

    from database import init_db, get_db
    from blueprints.auth.routes import init_auth_db
    from app import app

    init_db()
    init_auth_db()
    ensure_demo_admin(get_db())

    paths = [
        "/",
        "/recepcion/",
        "/recepcion/habitaciones",
        "/recepcion/canales",
        "/recepcion/fiscalidad",
        "/recepcion/tarifas",
        "/pisos/",
        "/pisos/habitaciones",
        "/compras/pedidos",
        "/compras/recepciones/validar",
        "/productos/",
        "/produccion/",
        "/produccion/semanal",
        "/stock/",
        "/mantenimiento/",
        "/mantenimiento/equipos",
        "/tpv/",
        "/contabilidad/",
        "/contabilidad/documentos",
        "/contabilidad/cuentas",
    ]
    results = []
    app.config["TESTING"] = False
    with app.test_client() as client:
        user = get_db().execute(
            """
            SELECT u.*, r.nombre AS rol_nombre
            FROM usuarios u
            LEFT JOIN roles r ON r.id = u.rol_id
            WHERE LOWER(u.email)=LOWER(?)
            """,
            (ADMIN_EMAIL,),
        ).fetchone()
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
            sess["user_nombre"] = user["nombre"]
            sess["user_email"] = user["email"]
            sess["user_rol"] = user["rol_nombre"] or "admin"
            sess["user_empresa_id"] = EMPRESA_ID
            sess["user_es_global"] = 1
            sess["puede_ver_precios"] = 1
            sess["puede_modificar_precios"] = 1
            sess["puede_validar_recepcion"] = 1
            sess["puede_validar_economicamente"] = 1
        for path in paths:
            try:
                response = client.get(path, follow_redirects=False)
                results.append({"path": path, "status": response.status_code})
            except Exception as exc:
                results.append({"path": path, "status": 500, "error": str(exc)[:200]})
    return results


def ensure_demo_admin(conn: sqlite3.Connection) -> None:
    from werkzeug.security import generate_password_hash

    role_id = id_by_name(conn, "roles", "admin", None)
    if not role_id:
        role_id = insert_row(conn, "roles", {"nombre": "admin"})
    values = {
        "nombre": "Administrador Demo",
        "email": ADMIN_EMAIL,
        "password_hash": generate_password_hash(ADMIN_PASSWORD),
        "rol_id": role_id,
        "empresa_id": EMPRESA_ID,
        "activo": 1,
        "codigo_acceso": "ADMINDEMO",
        "es_global": 1,
        "puede_ver_precios": 1,
        "puede_modificar_precios": 1,
        "puede_validar_recepcion": 1,
        "puede_validar_economicamente": 1,
    }
    upsert_by_keys(conn, "usuarios", ["email"], values)
    conn.commit()


def seed_empresa(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE empresas
        SET nombre=?, nif=?, direccion=?, telefono=?, email=?, logo=?, activo=1
        WHERE id=?
        """,
        (
            "El Raco Demo Hotel",
            "B12345678",
            "Avenida Hosteleria, 1",
            "+34 928 000 000",
            "demo@elraco.local",
            "brand/el-raco-logo.svg",
            EMPRESA_ID,
        ),
    )
    if conn.total_changes == 0:
        insert_row(
            conn,
            "empresas",
            {
                "id": EMPRESA_ID,
                "nombre": "El Raco Demo Hotel",
                "nif": "B12345678",
                "direccion": "Avenida Hosteleria, 1",
                "telefono": "+34 928 000 000",
                "email": "demo@elraco.local",
                "logo": "brand/el-raco-logo.svg",
                "activo": 1,
            },
        )


def seed_departments(conn: sqlite3.Connection) -> dict[str, int]:
    departments = [
        "COCINA CENTRAL",
        "BARES",
        "RESTAURANTES",
        "RECEPCION",
        "RRHH",
        "PISOS",
        "MANTENIMIENTO",
        "ADMINISTRACION",
        "COMPRAS / ALMACEN",
        "SNACK",
    ]
    mapping = {}
    for name in departments:
        mapping[name] = upsert_by_keys(
            conn,
            "departamentos",
            ["nombre", "empresa_id"],
            {"nombre": name, "descripcion": f"Departamento {name}", "activo": 1, "empresa_id": EMPRESA_ID},
        )

    subdeps = [
        ("CARNES Y PESCADOS", "COCINA CENTRAL"),
        ("COCINA CALIENTE", "COCINA CENTRAL"),
        ("CUARTO FRIO", "COCINA CENTRAL"),
        ("PASTELERIA", "COCINA CENTRAL"),
        ("DESAYUNOS", "COCINA CENTRAL"),
        ("BUFFET", "RESTAURANTES"),
        ("BAR PISCINA", "BARES"),
        ("LENCERIA", "PISOS"),
        ("LIMPIEZA HABITACIONES", "PISOS"),
        ("LIMPIEZA ZONAS COMUNES", "PISOS"),
        ("ECONOMATO", "COMPRAS / ALMACEN"),
        ("TALLER", "MANTENIMIENTO"),
    ]
    for name, dept_name in subdeps:
        upsert_by_keys(
            conn,
            "subdepartamentos",
            ["nombre", "empresa_id"],
            {
                "nombre": name,
                "descripcion": f"Subdepartamento {name}",
                "departamento_id": mapping[dept_name],
                "activo": 1,
                "empresa_id": EMPRESA_ID,
            },
        )
    return mapping


def ensure_puntos_venta(conn: sqlite3.Connection, dept: dict[str, int]) -> None:
    rows = [
        ("Buffet", "restaurante", dept["RESTAURANTES"]),
        ("Bar piscina", "bar", dept["BARES"]),
        ("Snack", "snack", dept["SNACK"]),
        ("Recepcion", "recepcion", dept["RECEPCION"]),
        ("Carta noche", "restaurante_carta", dept["RESTAURANTES"]),
    ]
    for name, tipo, departamento_id in rows:
        upsert_by_keys(
            conn,
            "puntos_venta",
            ["nombre", "empresa_id"],
            {
                "nombre": name,
                "tipo": tipo,
                "departamento_id": departamento_id,
                "activo": 1,
                "empresa_id": EMPRESA_ID,
            },
        )


def seed_room_types(conn: sqlite3.Connection) -> dict[str, int]:
    room_types = [
        ("IND", "Individual", 1, 1, 75),
        ("DOB", "Doble estandar", 2, 2, 118),
        ("SUP", "Doble superior", 2, 3, 145),
        ("FAM", "Familiar", 3, 4, 175),
        ("SUI", "Suite", 2, 4, 230),
    ]
    result = {}
    for code, name, base, max_pax, _price in room_types:
        result[code] = upsert_by_keys(
            conn,
            "recepcion_tipos_habitacion",
            ["codigo", "empresa_id"],
            {
                "codigo": code,
                "nombre": name,
                "capacidad_base": base,
                "capacidad_max": max_pax,
                "descripcion": f"Tipo {name}",
                "activo": 1,
                "empresa_id": EMPRESA_ID,
            },
        )
    return result


def seed_rooms(conn: sqlite3.Connection, total_rooms: int) -> int:
    room_types = seed_room_types(conn)
    created = 0
    for index in range(total_rooms):
        floor = index // 20 + 1
        position = index % 20 + 1
        number = f"{floor}{position:02d}"
        if position in (1, 2) and floor >= 3:
            code, cap = "SUI", 4
        elif position in (18, 19, 20):
            code, cap = "FAM", 4
        elif position in (5, 10, 15):
            code, cap = "SUP", 3
        elif position == 3 and floor == 1:
            code, cap = "IND", 1
        else:
            code, cap = "DOB", 2
        room_id = upsert_by_keys(
            conn,
            "habitaciones_recepcion",
            ["numero", "empresa_id"],
            {
                "numero": number,
                "nombre": f"Habitacion {number}",
                "tipo": code.lower(),
                "tipo_habitacion_id": room_types[code],
                "bloque": "A",
                "planta": str(floor),
                "capacidad": cap,
                "estado": "disponible",
                "observaciones": "Creada por simulador",
                "activo": 1,
                "empresa_id": EMPRESA_ID,
            },
        )
        upsert_by_keys(
            conn,
            "habitaciones_equipamiento",
            ["habitacion_id", "empresa_id"],
            {
                "habitacion_id": room_id,
                "tipo_cama": "matrimonio" if code in {"SUI", "SUP"} else "dos_separadas",
                "camas_matrimonio": 1 if code in {"SUI", "SUP"} else 0,
                "camas_individuales": 2 if code in {"DOB", "FAM"} else 1,
                "sofa_cama": 1 if code in {"FAM", "SUI"} else 0,
                "supletoria_posible": 1 if code in {"SUP", "FAM", "SUI"} else 0,
                "supletorias_max": 1 if code in {"SUP", "FAM"} else 2 if code == "SUI" else 0,
                "cuna_posible": 1,
                "tv": 1,
                "minibar": 1 if code in {"SUP", "FAM", "SUI"} else 0,
                "aire_tipo": "fan coil",
                "armario": 1,
                "mesas": 1,
                "sillas": 2,
                "mesillas": 2,
                "tumbonas": 2 if code in {"FAM", "SUI"} else 0,
                "tendedero": 1,
                "caja_fuerte": 1,
                "terraza": 1 if floor >= 2 else 0,
                "empresa_id": EMPRESA_ID,
            },
        )
        created += 1
    return created


def seed_canales_y_tarifas(conn: sqlite3.Connection, days: int) -> None:
    canales = [
        ("TUI", "TUI", "touroperador", 18.0, 80),
        ("BOOKING", "Booking", "ota", 15.0, 10),
        ("CENTRAL", "Central de reservas", "central", 8.0, 5),
        ("WEB", "Web propia", "directo", 0.0, 5),
    ]
    start = date.today()
    end = start + timedelta(days=days)
    for code, name, tipo, comision, porcentaje in canales:
        canal_id = upsert_by_keys(
            conn,
            "recepcion_canales_reserva",
            ["codigo", "empresa_id"],
            {
                "codigo": code,
                "nombre": name,
                "tipo": tipo,
                "comision_pct": comision,
                "activo": 1,
                "empresa_id": EMPRESA_ID,
                "config_json": json.dumps({"simulador": True}),
            },
        )
        for tipo_hab in conn.execute("SELECT id FROM recepcion_tipos_habitacion WHERE empresa_id=?", (EMPRESA_ID,)):
            exists = conn.execute(
                """
                SELECT id FROM recepcion_cupos_canal
                WHERE canal_id=? AND tipo_habitacion_id=? AND fecha_inicio=? AND fecha_fin=? AND empresa_id=?
                """,
                (canal_id, tipo_hab["id"], start.isoformat(), end.isoformat(), EMPRESA_ID),
            ).fetchone()
            if not exists:
                insert_row(
                    conn,
                    "recepcion_cupos_canal",
                    {
                        "canal_id": canal_id,
                        "tipo_habitacion_id": tipo_hab["id"],
                        "fecha_inicio": start.isoformat(),
                        "fecha_fin": end.isoformat(),
                        "porcentaje": porcentaje,
                        "habitaciones": 0,
                        "prioridad": 10 if code == "TUI" else 20,
                        "release_dias": 7 if code == "TUI" else 0,
                        "activo": 1,
                        "empresa_id": EMPRESA_ID,
                    },
                )

    prices = {
        "IND": 75,
        "DOB": 118,
        "SUP": 145,
        "FAM": 175,
        "SUI": 230,
    }
    supplements = {
        "solo_alojamiento": 0,
        "alojamiento_desayuno": 12,
        "media_pension": 32,
        "pension_completa": 52,
        "todo_incluido": 78,
    }
    for code, base in prices.items():
        tipo_id = id_by_codigo(conn, "recepcion_tipos_habitacion", code)
        if not tipo_id:
            continue
        for regimen, supplement in supplements.items():
            exists = conn.execute(
                """
                SELECT id FROM recepcion_tarifas_tipo
                WHERE tipo_habitacion_id=? AND regimen=? AND fecha_inicio=? AND fecha_fin=? AND empresa_id=?
                """,
                (tipo_id, regimen, start.isoformat(), end.isoformat(), EMPRESA_ID),
            ).fetchone()
            if not exists:
                insert_row(
                    conn,
                    "recepcion_tarifas_tipo",
                    {
                        "tipo_habitacion_id": tipo_id,
                        "regimen": regimen,
                        "fecha_inicio": start.isoformat(),
                        "fecha_fin": end.isoformat(),
                        "precio_noche": base + supplement,
                        "prioridad": 10,
                        "activo": 1,
                        "empresa_id": EMPRESA_ID,
                    },
                )


def seed_proveedores_productos(conn: sqlite3.Connection, dept: dict[str, int]) -> dict[str, int]:
    proveedores = [
        ("Carnes Congeladas Canarias", "B76000001", "carnes@demo.local", "albaran"),
        ("Pescados Atlantico", "B76000002", "pescados@demo.local", "albaran"),
        ("Frutas La Vega", "B76000003", "frutas@demo.local", "factura"),
        ("Limpiezas Insulares", "B76000004", "limpieza@demo.local", "factura"),
        ("Papeleria Hotel", "B76000005", "papeleria@demo.local", "factura"),
        ("Bebidas Norte", "B76000006", "bebidas@demo.local", "albaran"),
        ("Repuestos Hoteleros", "B76000007", "repuestos@demo.local", "factura"),
    ]
    prov_ids = {}
    for name, nif, email, doc in proveedores:
        prov_ids[name] = upsert_by_keys(
            conn,
            "proveedores",
            ["nombre", "empresa_id"],
            {
                "nombre": name,
                "nif": nif,
                "email": email,
                "telefono": "+34 928 000 000",
                "direccion": "Zona industrial demo",
                "contacto": "Departamento comercial",
                "observaciones": "Proveedor demo",
                "activo": 1,
                "empresa_id": EMPRESA_ID,
                "documento_habitual": doc,
                "facturacion_habitual": "mensual" if doc == "albaran" else "por_documento",
                "forma_pago": "transferencia",
                "dias_pago": 30,
            },
        )

    products = [
        ("Pollo entero congelado", "carnes", "kg", "congelado", 3.2, 80, "Carnes Congeladas Canarias", "", 0, 72),
        ("Pechuga de pollo congelada", "carnes", "kg", "congelado", 5.9, 60, "Carnes Congeladas Canarias", "", 0, 72),
        ("Ternera para guisar congelada", "carnes", "kg", "congelado", 8.7, 40, "Carnes Congeladas Canarias", "", 0, 72),
        ("Merluza congelada", "pescados", "kg", "congelado", 7.6, 45, "Pescados Atlantico", "pescado", 0, 72),
        ("Lomo de salmon congelado", "pescados", "kg", "congelado", 12.4, 25, "Pescados Atlantico", "pescado", 0, 72),
        ("Lechuga iceberg", "verduras", "kg", "refrigerado", 1.6, 30, "Frutas La Vega", "", 0, 0),
        ("Tomate ensalada", "verduras", "kg", "refrigerado", 2.1, 40, "Frutas La Vega", "", 0, 0),
        ("Pimiento rojo", "verduras", "kg", "refrigerado", 2.8, 20, "Frutas La Vega", "", 0, 0),
        ("Manzana golden", "frutas", "kg", "refrigerado", 1.9, 30, "Frutas La Vega", "", 0, 0),
        ("Arroz largo", "secos", "kg", "seco", 1.4, 60, "Frutas La Vega", "", 0, 0),
        ("Aceite de oliva", "secos", "l", "seco", 5.5, 40, "Frutas La Vega", "", 0, 0),
        ("Harina de trigo", "secos", "kg", "seco", 0.9, 30, "Frutas La Vega", "gluten", 0, 0),
        ("Leche entera", "lacteos", "l", "refrigerado", 1.05, 50, "Frutas La Vega", "lacteos", 0, 0),
        ("Detergente lavanderia", "limpieza", "l", "quimico", 2.8, 20, "Limpiezas Insulares", "", 0, 0),
        ("Desinfectante superficies", "limpieza", "l", "quimico", 3.4, 25, "Limpiezas Insulares", "", 0, 0),
        ("Papel higienico industrial", "papeleria", "paquete", "seco", 11.0, 25, "Papeleria Hotel", "", 0, 0),
        ("Toner recepcion", "papeleria", "ud", "seco", 54.0, 2, "Papeleria Hotel", "", 0, 0),
        ("Agua 0.5L", "bebidas", "ud", "seco", 0.28, 200, "Bebidas Norte", "", 1, 0),
        ("Refresco cola lata", "bebidas", "ud", "seco", 0.48, 180, "Bebidas Norte", "", 1, 0),
        ("Cerveza barril", "bebidas", "l", "refrigerado", 1.9, 100, "Bebidas Norte", "gluten", 1, 0),
        ("Vino blanco botella", "bebidas", "ud", "seco", 3.2, 60, "Bebidas Norte", "sulfitos", 1, 0),
        ("Cerradura magnetica habitacion", "mantenimiento", "ud", "seco", 38.0, 4, "Repuestos Hoteleros", "", 0, 0),
        ("Filtro fan coil", "mantenimiento", "ud", "seco", 7.5, 20, "Repuestos Hoteleros", "", 0, 0),
        ("Bombilla LED E27", "mantenimiento", "ud", "seco", 2.2, 30, "Repuestos Hoteleros", "", 0, 0),
    ]
    product_ids = {}
    for name, family, unit, conservation, price, minimum, proveedor, alergenos, visible_venta, descongela in products:
        product_id = upsert_by_keys(
            conn,
            "productos",
            ["nombre", "empresa_id"],
            {
                "nombre": name,
                "unidad": unit,
                "tipo_conservacion": conservation,
                "familia": family,
                "precio": price,
                "stock_minimo": minimum,
                "codigo": "SIM-" + str(abs(hash(name)) % 100000).zfill(5),
                "ubicacion": family,
                "alergenos": alergenos,
                "alergenos_validado": 1 if alergenos else 0,
                "activo": 1,
                "visible_en_compras": 1,
                "visible_en_produccion": 1 if family in {"carnes", "pescados", "verduras", "frutas", "secos", "lacteos"} else 0,
                "visible_en_venta": visible_venta,
                "tiempo_descongelado_horas": descongela,
                "requiere_desinfeccion": 1 if family in {"verduras", "frutas"} else 0,
                "empresa_id": EMPRESA_ID,
                "proveedor_id": prov_ids[proveedor],
            },
        )
        product_ids[name] = product_id
        upsert_by_keys(
            conn,
            "productos_proveedores",
            ["producto_id", "proveedor_id"],
            {
                "producto_id": product_id,
                "proveedor_id": prov_ids[proveedor],
                "precio": price,
                "precio_compra": price,
                "precio_ultimo": price,
                "precio_vigente": price,
                "precio_medio": price,
                "activo": 1,
                "preferente": 1,
                "es_principal": 1,
            },
        )
    return product_ids


def seed_almacenes_stock(conn: sqlite3.Connection, dept: dict[str, int], products: dict[str, int]) -> dict[str, int]:
    almacenes = [
        ("Economato seco cocina", "seco", "Planta -1", dept["COCINA CENTRAL"], None, 0, None, None),
        ("Camara verduras", "refrigerado", "Cocina", dept["COCINA CENTRAL"], None, 1, 0, 5),
        ("Camara carnes congelados", "congelado", "Cocina", dept["COCINA CENTRAL"], None, 1, -24, -18),
        ("Camara pescados congelados", "congelado", "Cocina", dept["COCINA CENTRAL"], None, 1, -24, -18),
        ("Camara descongelacion", "refrigerado", "Cocina", dept["COCINA CENTRAL"], None, 1, 0, 5),
        ("Camara elaborados", "refrigerado", "Cocina", dept["COCINA CENTRAL"], None, 1, 0, 5),
        ("Almacen bebidas bares", "seco", "Bar piscina", dept["BARES"], None, 0, None, None),
        ("Office lenceria", "seco", "Pisos", dept["PISOS"], None, 0, None, None),
        ("Almacen limpieza pisos", "quimico", "Pisos", dept["PISOS"], None, 0, None, None),
        ("Taller mantenimiento", "seco", "Mantenimiento", dept["MANTENIMIENTO"], None, 0, None, None),
    ]
    almacen_ids = {}
    for name, tipo, ubicacion, departamento_id, subdepartamento_id, temp, rmin, rmax in almacenes:
        almacen_ids[name] = upsert_by_keys(
            conn,
            "almacenes",
            ["nombre", "empresa_id"],
            {
                "nombre": name,
                "tipo": tipo,
                "ubicacion": ubicacion,
                "responsable": "Simulador",
                "departamento_id": departamento_id,
                "subdepartamento_id": subdepartamento_id,
                "requiere_control_temperatura": temp,
                "rango_min": rmin,
                "rango_max": rmax,
                "activo": 1,
                "empresa_id": EMPRESA_ID,
            },
        )

    placement = {
        "carnes": "Camara carnes congelados",
        "pescados": "Camara pescados congelados",
        "verduras": "Camara verduras",
        "frutas": "Camara verduras",
        "bebidas": "Almacen bebidas bares",
        "limpieza": "Almacen limpieza pisos",
        "papeleria": "Office lenceria",
        "mantenimiento": "Taller mantenimiento",
    }
    for row in conn.execute("SELECT id, nombre, familia, unidad, precio FROM productos WHERE empresa_id=?", (EMPRESA_ID,)):
        almacen_name = placement.get(row["familia"], "Economato seco cocina")
        almacen_id = almacen_ids[almacen_name]
        if row["familia"] in {"carnes", "pescados"}:
            qty = random.choice([25, 35, 50, 80])
            cad = date.today() + timedelta(days=random.randint(90, 180))
        elif row["familia"] in {"verduras", "frutas", "lacteos"}:
            qty = random.choice([20, 30, 45, 60])
            cad = date.today() + timedelta(days=random.randint(5, 14))
        elif row["familia"] == "bebidas":
            qty = random.choice([120, 180, 240])
            cad = date.today() + timedelta(days=random.randint(120, 360))
        else:
            qty = random.choice([15, 30, 60, 100])
            cad = date.today() + timedelta(days=random.randint(90, 300))
        upsert_by_keys(
            conn,
            "stock_almacen",
            ["producto_id", "almacen_id"],
            {"producto_id": row["id"], "almacen_id": almacen_id, "cantidad": qty, "stock_minimo": max(5, qty * 0.2)},
        )
        conn.execute("UPDATE productos SET cantidad=? WHERE id=?", (qty, row["id"]))
        if not conn.execute("SELECT 1 FROM lotes WHERE producto_id=? AND empresa_id=? LIMIT 1", (row["id"], EMPRESA_ID)).fetchone():
            lote = f"REC-{str(row['id']).zfill(3)}-{random.randint(100, 999)}"
            insert_row(
                conn,
                "lotes",
                {
                    "codigo_lote": lote,
                    "producto_id": row["id"],
                    "cantidad_inicial": qty,
                    "cantidad_actual": qty,
                    "fecha_recepcion": date.today().isoformat(),
                    "fecha_caducidad": cad.isoformat(),
                    "proveedor_id": conn.execute("SELECT proveedor_id FROM productos WHERE id=?", (row["id"],)).fetchone()["proveedor_id"],
                    "almacen_id": almacen_id,
                    "temperatura_recepcion": -19 if row["familia"] in {"carnes", "pescados"} else 4 if row["familia"] in {"verduras", "frutas", "lacteos"} else None,
                    "lote_proveedor": lote,
                    "origen_lote": "proveedor",
                    "albaran": "ALB-SIM",
                    "estado": "activo",
                    "responsable_recepcion": "Simulador",
                    "ubicacion_actual": almacen_name,
                    "empresa_id": EMPRESA_ID,
                },
            )
    return almacen_ids


def seed_reservas(conn: sqlite3.Connection, total_days: int, target_occupancy: float) -> int:
    rooms = conn.execute(
        """
        SELECT h.*, th.codigo AS tipo_codigo
        FROM habitaciones_recepcion h
        LEFT JOIN recepcion_tipos_habitacion th ON th.id=h.tipo_habitacion_id
        WHERE h.empresa_id=? AND COALESCE(h.activo,1)=1
        ORDER BY h.numero
        """,
        (EMPRESA_ID,),
    ).fetchall()
    canales = conn.execute("SELECT id, codigo, comision_pct FROM recepcion_canales_reserva WHERE empresa_id=? AND activo=1", (EMPRESA_ID,)).fetchall()
    if not rooms or not canales:
        return 0
    first_day = date.today()
    names = ["Pablo Juan", "Maria Garcia", "Laura Smith", "Hans Muller", "Giulia Rossi", "Ana Lopez", "Carlos Medina"]
    regimens = ["alojamiento_desayuno", "media_pension", "pension_completa", "todo_incluido", "solo_alojamiento"]
    base_prices = {"IND": 75, "DOB": 118, "SUP": 145, "FAM": 175, "SUI": 230}
    supplements = {"solo_alojamiento": 0, "alojamiento_desayuno": 12, "media_pension": 32, "pension_completa": 52, "todo_incluido": 78}
    used = {room["id"]: [] for room in rooms}
    created = 0
    for room in rooms:
        cursor_day = first_day
        while cursor_day < first_day + timedelta(days=total_days):
            if random.random() > target_occupancy:
                cursor_day += timedelta(days=1)
                continue
            nights = random.randint(2, 6)
            salida = min(cursor_day + timedelta(days=nights), first_day + timedelta(days=total_days + 2))
            if any(not (salida <= start or cursor_day >= end) for start, end in used[room["id"]]):
                cursor_day += timedelta(days=1)
                continue
            used[room["id"]].append((cursor_day, salida))
            created += 1
            canal = random.choice(canales)
            regimen = random.choice(regimens)
            adults = 1 if room["capacidad"] == 1 else random.choice([2, 2, 2, min(3, room["capacidad"])])
            minors = 0 if room["capacidad"] <= adults else random.choice([0, 1])
            pax = adults + minors
            noches = max(1, (salida - cursor_day).days)
            tipo_codigo = room["tipo_codigo"] or "DOB"
            total = dinero(noches * (base_prices.get(tipo_codigo, 118) + supplements[regimen] * pax))
            status = "checkout" if salida < first_day else "alojado" if cursor_day <= first_day < salida else "confirmada"
            code = f"RSIM-{first_day.strftime('%Y%m')}-{created:04d}"
            reserva_id = insert_row(
                conn,
                "reservas_recepcion",
                {
                    "codigo": code,
                    "habitacion_id": room["id"],
                    "tipo_habitacion_id": room["tipo_habitacion_id"],
                    "fecha_entrada": cursor_day.isoformat(),
                    "fecha_salida": salida.isoformat(),
                    "hora_llegada": "14:00",
                    "hora_salida": "11:00",
                    "regimen": regimen,
                    "adultos": adults,
                    "menores": minors,
                    "estado": status,
                    "origen": canal["codigo"].lower(),
                    "titular_nombre": random.choice(names),
                    "titular_email": f"cliente{created}@demo.local",
                    "titular_telefono": "+34 600 000 000",
                    "precio_total": total,
                    "deposito": dinero(total * 0.25) if status == "confirmada" else total,
                    "saldo": 0 if status in {"alojado", "checkout"} else dinero(total * 0.75),
                    "observaciones": "Reserva creada por simulador",
                    "alergias_resumen": "Celiaco" if random.random() < 0.06 else "",
                    "empresa_id": EMPRESA_ID,
                    "canal_id": canal["id"],
                    "localizador_externo": f"{canal['codigo']}-{100000 + created}",
                    "comision_canal": dinero(total * float(canal["comision_pct"] or 0) / 100),
                    "politica_credito": "fianza_virtual" if random.random() < 0.35 else "abierto",
                    "limite_credito": 150 if random.random() < 0.35 else 0,
                    "fianza_virtual": 100 if random.random() < 0.25 else 0,
                },
            )
            insert_viajeros(conn, reserva_id, adults, minors, created)
            if status in {"alojado", "checkout"}:
                insert_row(
                    conn,
                    "pagos_reserva_recepcion",
                    {
                        "reserva_id": reserva_id,
                        "fecha": cursor_day.isoformat(),
                        "concepto": "Pago estancia",
                        "importe": total,
                        "forma_pago": random.choice(["tarjeta", "efectivo", "transferencia"]),
                        "referencia": f"PAY-SIM-{created:04d}",
                        "estado": "confirmado",
                        "empresa_id": EMPRESA_ID,
                        "usuario_id": 1,
                    },
                )
            if status == "checkout" and created % 3 == 0:
                crear_factura_reserva(conn, reserva_id, total, created)
            cursor_day = salida + timedelta(days=random.randint(0, 2))
    update_room_statuses(conn)
    return created


def insert_viajeros(conn: sqlite3.Connection, reserva_id: int, adults: int, minors: int, serial: int) -> None:
    total = adults + minors
    for idx in range(total):
        is_minor = idx >= adults
        birth_year = date.today().year - (random.randint(4, 12) if is_minor else random.randint(25, 70))
        doc = "" if is_minor and random.randint(0, 1) else f"SIM{serial:04d}{idx}"
        insert_row(
            conn,
            "viajeros_recepcion",
            {
                "reserva_id": reserva_id,
                "nombre": ["Pablo", "Maria", "Laura", "Hans", "Giulia", "Ana"][idx % 6],
                "apellidos": f"Demo {serial}",
                "fecha_nacimiento": f"{birth_year}-05-10",
                "nacionalidad": random.choice(["ES", "DE", "IT", "GB", "FR"]),
                "sexo": random.choice(["M", "F"]),
                "tipo_documento": "DNI" if doc else "",
                "numero_documento": doc,
                "soporte_documento": "",
                "parentesco": "Titular" if idx == 0 else "Acompanante",
                "telefono": "+34 600 000 000" if idx == 0 else "",
                "email": f"viajero{serial}_{idx}@demo.local" if idx == 0 else "",
                "direccion": "Direccion demo",
                "codigo_postal": "35000",
                "localidad": "Las Palmas",
                "pais": "Espana",
                "es_titular": 1 if idx == 0 else 0,
                "alergias_intolerancias": "Gluten" if idx == 0 and random.random() < 0.04 else "",
                "avisos_departamentos": "cocina" if idx == 0 and random.random() < 0.04 else "",
                "registro_viajeros_estado": "pendiente",
                "empresa_id": EMPRESA_ID,
            },
        )


def update_room_statuses(conn: sqlite3.Connection) -> None:
    today = date.today().isoformat()
    conn.execute("UPDATE habitaciones_recepcion SET estado='disponible' WHERE empresa_id=?", (EMPRESA_ID,))
    conn.execute(
        """
        UPDATE habitaciones_recepcion
        SET estado='ocupada'
        WHERE id IN (
            SELECT habitacion_id
            FROM reservas_recepcion
            WHERE empresa_id=?
              AND habitacion_id IS NOT NULL
              AND fecha_entrada <= ?
              AND fecha_salida > ?
              AND estado IN ('checkin','alojado','confirmada')
        )
        """,
        (EMPRESA_ID, today, today),
    )


def crear_factura_reserva(conn: sqlite3.Connection, reserva_id: int, total: float, serial: int) -> None:
    base = dinero(total / 1.07)
    tax = dinero(total - base)
    factura_id = insert_row(
        conn,
        "recepcion_facturas",
        {
            "reserva_id": reserva_id,
            "tipo": "factura",
            "serie": "REC",
            "ejercicio": date.today().year,
            "numero": serial,
            "numero_completo": f"REC-{date.today().year}-{serial:04d}",
            "fecha": date.today().isoformat(),
            "cliente_nombre": conn.execute("SELECT titular_nombre FROM reservas_recepcion WHERE id=?", (reserva_id,)).fetchone()["titular_nombre"],
            "cliente_nif": f"X{serial:07d}Z",
            "cliente_email": f"cliente{serial}@demo.local",
            "idioma": "es",
            "impuesto_jurisdiccion": "IGIC",
            "base": base,
            "impuestos": tax,
            "total": total,
            "pagado": total,
            "saldo": 0,
            "estado": "emitida",
            "empresa_id": EMPRESA_ID,
            "usuario_id": 1,
        },
    )
    insert_row(
        conn,
        "recepcion_factura_lineas",
        {
            "factura_id": factura_id,
            "origen": "reserva",
            "origen_id": reserva_id,
            "concepto": "Estancia hotelera",
            "cantidad": 1,
            "precio_unitario": base,
            "base": base,
            "impuesto_pct": 7,
            "impuesto_codigo": "IGIC",
            "impuesto_nombre": "IGIC general",
            "impuesto": tax,
            "total": total,
            "empresa_id": EMPRESA_ID,
        },
    )


def seed_recetas_menus_produccion(conn: sqlite3.Connection, products: dict[str, int], dept: dict[str, int], days: int) -> int:
    recipes = {
        "Pollo asado al romero": [("Pollo entero congelado", 1.4), ("Aceite de oliva", 0.08), ("Pimiento rojo", 0.2)],
        "Merluza al horno": [("Merluza congelada", 0.9), ("Aceite de oliva", 0.06), ("Tomate ensalada", 0.25)],
        "Ensalada mixta": [("Lechuga iceberg", 0.6), ("Tomate ensalada", 0.5), ("Aceite de oliva", 0.04)],
        "Arroz blanco": [("Arroz largo", 0.45), ("Aceite de oliva", 0.03)],
        "Fruta cortada": [("Manzana golden", 0.9)],
        "Crema de verduras": [("Pimiento rojo", 0.25), ("Tomate ensalada", 0.4), ("Leche entera", 0.3)],
    }
    recipe_ids = {}
    for name, ingredientes in recipes.items():
        recipe_id = upsert_by_keys(
            conn,
            "recetas",
            ["nombre", "empresa_id"],
            {
                "nombre": name,
                "descripcion": "Receta demo para 4 raciones",
                "alergenos": "pescado" if "Merluza" in name else "lacteos" if "Crema" in name else "",
                "categoria": "buffet",
                "raciones_base": 4,
                "empresa_id": EMPRESA_ID,
            },
        )
        recipe_ids[name] = recipe_id
        for product_name, qty in ingredientes:
            producto_id = products.get(product_name)
            if not producto_id:
                continue
            existing = conn.execute(
                "SELECT id FROM recetas_ingredientes WHERE receta_id=? AND producto_id=?",
                (recipe_id, producto_id),
            ).fetchone()
            if existing:
                conn.execute("UPDATE recetas_ingredientes SET cantidad=? WHERE id=?", (qty, existing["id"]))
            else:
                insert_row(conn, "recetas_ingredientes", {"receta_id": recipe_id, "producto_id": producto_id, "cantidad": qty})

    tipo_menu_ids = {row["nombre"].upper(): row["id"] for row in conn.execute("SELECT id, nombre FROM tipos_menu WHERE empresa_id=?", (EMPRESA_ID,))}
    buffet_pv = id_by_name(conn, "puntos_venta", "Buffet") or 1
    cocina_caliente = id_by_name(conn, "subdepartamentos", "COCINA CALIENTE")
    cuarto_frio = id_by_name(conn, "subdepartamentos", "CUARTO FRIO")
    pasteleria = id_by_name(conn, "subdepartamentos", "PASTELERIA")
    created_parts = 0
    for offset in range(min(days, 14)):
        fecha = (date.today() + timedelta(days=offset)).isoformat()
        for servicio in ("DESAYUNO", "ALMUERZO", "CENA"):
            tipo_id = tipo_menu_ids.get(servicio)
            if not tipo_id:
                continue
            menu_id = upsert_by_keys(
                conn,
                "menus_diarios",
                ["fecha", "tipo_menu_id", "empresa_id"],
                {
                    "fecha": fecha,
                    "tipo_menu_id": tipo_id,
                    "departamento_id": dept["COCINA CENTRAL"],
                    "punto_venta_id": buffet_pv,
                    "modo_servicio": "buffet",
                    "observaciones": "Menu demo generado para simulacion",
                    "empresa_id": EMPRESA_ID,
                },
            )
            sections = [
                ("Caliente", cocina_caliente, ["Pollo asado al romero", "Merluza al horno", "Arroz blanco"]),
                ("Frio", cuarto_frio, ["Ensalada mixta"]),
                ("Postres", pasteleria, ["Fruta cortada"]),
            ]
            for order, (section, subdep, names) in enumerate(sections, start=1):
                if not subdep:
                    continue
                menu_sub_id = upsert_by_keys(
                    conn,
                    "menu_subdepartamentos",
                    ["menu_diario_id", "subdepartamento_id", "seccion"],
                    {"menu_diario_id": menu_id, "subdepartamento_id": subdep, "seccion": section, "orden": order},
                )
                for plato_order, recipe_name in enumerate(names, start=1):
                    recipe_id = recipe_ids[recipe_name]
                    exists = conn.execute(
                        "SELECT id FROM menu_platos WHERE menu_subdepartamento_id=? AND receta_id=?",
                        (menu_sub_id, recipe_id),
                    ).fetchone()
                    if not exists:
                        insert_row(conn, "menu_platos", {"menu_subdepartamento_id": menu_sub_id, "receta_id": recipe_id, "orden": plato_order})
            if offset < 7:
                pax = pax_servicio(conn, fecha, servicio.lower())
                parte_id = upsert_by_keys(
                    conn,
                    "partes_produccion",
                    ["fecha", "menu_diario_id", "empresa_id"],
                    {
                        "fecha": fecha,
                        "fecha_prevista": fecha,
                        "hora": "08:00" if servicio == "DESAYUNO" else "12:00" if servicio == "ALMUERZO" else "18:00",
                        "responsable": "Chef demo",
                        "turno": servicio.lower(),
                        "punto_venta_id": buffet_pv,
                        "observaciones": f"Produccion {servicio} para {pax} pax",
                        "estado": "planificado",
                        "menu_diario_id": menu_id,
                        "pax_previstos": pax,
                        "regla_calculo": "buffet_demo",
                        "empresa_id": EMPRESA_ID,
                    },
                )
                for recipe_name in ["Pollo asado al romero", "Merluza al horno", "Ensalada mixta", "Arroz blanco", "Fruta cortada"]:
                    recipe_id = recipe_ids[recipe_name]
                    raciones = calcular_raciones_buffet(recipe_name, pax)
                    cost = coste_receta(conn, recipe_id, raciones)
                    exists = conn.execute(
                        "SELECT id FROM lineas_produccion WHERE parte_id=? AND receta_id=?",
                        (parte_id, recipe_id),
                    ).fetchone()
                    values = {
                        "parte_id": parte_id,
                        "receta_id": recipe_id,
                        "cantidad_planificada": raciones,
                        "cantidad_real": 0,
                        "cantidad_producida": 0,
                        "coste_estimado": cost,
                        "coste_real": 0,
                        "coste_unitario_teorico": dinero(cost / raciones) if raciones else 0,
                        "empresa_id": EMPRESA_ID,
                        "estado": "pendiente",
                        "raciones_planificadas": raciones,
                        "pax_previstos": pax,
                        "estacion_buffet": "Caliente" if recipe_name in {"Pollo asado al romero", "Merluza al horno"} else "Frio/Postres",
                        "porcentaje_prevision": 30 if recipe_name in {"Pollo asado al romero", "Merluza al horno"} else 20,
                        "regla_calculo": "buffet_alta_variedad",
                    }
                    if exists:
                        set_cols = ", ".join([f"{key}=?" for key in filter_values(conn, "lineas_produccion", values).keys() if key not in {"parte_id", "receta_id"}])
                        vals = [value for key, value in filter_values(conn, "lineas_produccion", values).items() if key not in {"parte_id", "receta_id"}]
                        conn.execute(f"UPDATE lineas_produccion SET {set_cols} WHERE id=?", vals + [exists["id"]])
                    else:
                        insert_row(conn, "lineas_produccion", values)
                created_parts += 1
    return created_parts


def pax_servicio(conn: sqlite3.Connection, fecha: str, servicio: str) -> int:
    rows = conn.execute(
        """
        SELECT adultos, menores, regimen, fecha_entrada, fecha_salida
        FROM reservas_recepcion
        WHERE empresa_id=? AND fecha_entrada <= ? AND fecha_salida > ?
          AND estado IN ('confirmada','checkin','alojado')
        """,
        (EMPRESA_ID, fecha, fecha),
    ).fetchall()
    total = 0
    for row in rows:
        pax = int(row["adultos"] or 0) + int(row["menores"] or 0)
        regimen = row["regimen"]
        entrada = row["fecha_entrada"] == fecha
        salida = row["fecha_salida"] == fecha
        if servicio == "desayuno" and regimen in {"alojamiento_desayuno", "media_pension", "pension_completa", "todo_incluido"}:
            total += pax
        elif servicio == "almuerzo" and regimen in {"pension_completa", "todo_incluido"}:
            total += pax + (pax // 5 if entrada else 0)
        elif servicio == "cena" and regimen in {"media_pension", "pension_completa", "todo_incluido"}:
            total += pax
        elif not salida:
            total += pax
    return max(total, 40)


def calcular_raciones_buffet(recipe_name: str, pax: int) -> float:
    if recipe_name in {"Pollo asado al romero", "Merluza al horno"}:
        return round(pax * 0.28)
    if recipe_name == "Ensalada mixta":
        return round(pax * 0.18)
    if recipe_name == "Arroz blanco":
        return round(pax * 0.22)
    return round(pax * 0.18)


def coste_receta(conn: sqlite3.Connection, recipe_id: int, raciones: float) -> float:
    fm = float(raciones or 0) / 4.0
    total = 0.0
    for row in conn.execute(
        """
        SELECT ri.cantidad, p.precio
        FROM recetas_ingredientes ri
        JOIN productos p ON p.id=ri.producto_id
        WHERE ri.receta_id=?
        """,
        (recipe_id,),
    ):
        total += float(row["cantidad"] or 0) * float(row["precio"] or 0) * fm
    return dinero(total)


def seed_compras(conn: sqlite3.Connection, dept: dict[str, int], products: dict[str, int]) -> int:
    pedidos = [
        ("Carnes Congeladas Canarias", "COCINA CENTRAL", "enviado", [("Pollo entero congelado", 80), ("Pechuga de pollo congelada", 45)]),
        ("Pescados Atlantico", "COCINA CENTRAL", "recibido", [("Merluza congelada", 50), ("Lomo de salmon congelado", 20)]),
        ("Frutas La Vega", "COCINA CENTRAL", "cerrado", [("Lechuga iceberg", 35), ("Tomate ensalada", 55), ("Manzana golden", 40)]),
        ("Limpiezas Insulares", "PISOS", "enviado", [("Detergente lavanderia", 25), ("Desinfectante superficies", 30)]),
        ("Papeleria Hotel", "RECEPCION", "pendiente", [("Toner recepcion", 3), ("Papel higienico industrial", 20)]),
        ("Bebidas Norte", "BARES", "parcial", [("Agua 0.5L", 240), ("Refresco cola lata", 180), ("Cerveza barril", 100)]),
        ("Repuestos Hoteleros", "MANTENIMIENTO", "enviado", [("Filtro fan coil", 40), ("Bombilla LED E27", 60)]),
    ]
    created = 0
    for idx, (prov_name, dept_name, estado, lineas) in enumerate(pedidos, start=1):
        prov_id = id_by_name(conn, "proveedores", prov_name)
        departamento_id = dept[dept_name]
        numero = f"PED-SIM-{date.today().strftime('%Y%m%d')}-{idx:03d}"
        pedido_id = upsert_by_keys(
            conn,
            "pedidos",
            ["numero_pedido"],
            {
                "numero_pedido": numero,
                "proveedor_id": prov_id,
                "departamento_id": departamento_id,
                "departamento_solicitante_id": departamento_id,
                "fecha_pedido": (date.today() - timedelta(days=idx)).isoformat(),
                "fecha_entrega": (date.today() + timedelta(days=2)).isoformat(),
                "estado": estado,
                "estado_recepcion": "recibido" if estado in {"recibido", "cerrado"} else "pendiente",
                "observaciones": "Pedido demo",
                "creado_por": "Simulador",
                "solicitado_por": "Simulador",
                "empresa_id": EMPRESA_ID,
                "cerrado": 1 if estado == "cerrado" else 0,
            },
        )
        created += 1
        for product_name, qty in lineas:
            producto_id = products[product_name]
            price = conn.execute("SELECT precio FROM productos WHERE id=?", (producto_id,)).fetchone()["precio"] or 0
            existing = conn.execute(
                "SELECT id FROM lineas_pedido WHERE pedido_id=? AND producto_id=?",
                (pedido_id, producto_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE lineas_pedido SET cantidad_solicitada=?, precio_unitario=? WHERE id=?",
                    (qty, price, existing["id"]),
                )
            else:
                insert_row(
                    conn,
                    "lineas_pedido",
                    {"pedido_id": pedido_id, "producto_id": producto_id, "cantidad_solicitada": qty, "cantidad_recibida": qty if estado in {"recibido", "cerrado"} else 0, "precio_unitario": price},
                )
        if estado in {"recibido", "cerrado"} and not conn.execute("SELECT 1 FROM recepciones WHERE pedido_id=? LIMIT 1", (pedido_id,)).fetchone():
            recepcion_id = insert_row(
                conn,
                "recepciones",
                {
                    "pedido_id": pedido_id,
                    "proveedor_id": prov_id,
                    "departamento_id": departamento_id,
                    "departamento_receptor_id": departamento_id,
                    "fecha_recepcion": date.today().isoformat(),
                    "numero_albaran": f"ALB-SIM-{idx:03d}",
                    "observaciones": "Recepcion simulada",
                    "recibido_por": "Simulador",
                    "empresa_id": EMPRESA_ID,
                    "tipo_recepcion": "albaran",
                    "validada_operativamente": 1,
                    "validada_economicamente": 1 if estado == "cerrado" else 0,
                    "stock_aplicado": 1,
                    "documento_economico_tipo": "albaran",
                    "estado_facturacion": "pendiente_factura" if estado == "recibido" else "factura_recibida",
                },
            )
            for linea in conn.execute("SELECT * FROM lineas_pedido WHERE pedido_id=?", (pedido_id,)):
                insert_row(
                    conn,
                    "lineas_recepcion",
                    {
                        "recepcion_id": recepcion_id,
                        "producto_id": linea["producto_id"],
                        "cantidad_recibida": linea["cantidad_solicitada"],
                        "lote": f"SIM-{pedido_id}-{linea['producto_id']}",
                        "fecha_caducidad": (date.today() + timedelta(days=90)).isoformat(),
                        "temperatura": 4,
                        "precio_unitario": linea["precio_unitario"],
                        "importe_total": dinero(linea["cantidad_solicitada"] * linea["precio_unitario"]),
                        "num_envases": max(1, int(float(linea["cantidad_solicitada"] or 1) // 10)),
                        "aceptado": 1,
                        "empresa_id": EMPRESA_ID,
                    },
                )
    return created


def seed_mantenimiento(conn: sqlite3.Connection, dept: dict[str, int], almacenes: dict[str, int]) -> int:
    equipos = [
        ("EQ-CAM-CARNES", "Camara carnes congelados", "camara_congelacion", "alta", dept["COCINA CENTRAL"], "Cocina", almacenes.get("Camara carnes congelados"), 1),
        ("EQ-CAM-VERD", "Camara verduras", "camara_refrigeracion", "alta", dept["COCINA CENTRAL"], "Cocina", almacenes.get("Camara verduras"), 1),
        ("EQ-HORNO-01", "Horno mixto cocina caliente", "horno", "alta", dept["COCINA CENTRAL"], "Cocina caliente", None, 1),
        ("EQ-LAV-01", "Lavadora industrial", "lavanderia", "media", dept["PISOS"], "Lavanderia", None, 0),
        ("EQ-ASC-01", "Ascensor clientes", "ascensor", "alta", dept["MANTENIMIENTO"], "Hall principal", None, 0),
        ("EQ-FC-201", "Fan coil habitacion 201", "climatizacion", "media", dept["MANTENIMIENTO"], "Habitacion 201", None, 0),
    ]
    equipo_ids = {}
    for codigo, nombre, tipo, criticidad, departamento_id, ubicacion, almacen_id, requiere_appcc in equipos:
        equipo_ids[codigo] = upsert_by_keys(
            conn,
            "equipos_mantenimiento",
            ["codigo", "empresa_id"],
            {
                "codigo": codigo,
                "nombre": nombre,
                "tipo": tipo,
                "familia": "frio" if "Camara" in nombre else tipo,
                "categoria_operativa": "appcc" if requiere_appcc else "mantenimiento",
                "clase_activo": "equipo",
                "criticidad": criticidad,
                "inventariable": 1,
                "requiere_appcc": requiere_appcc,
                "origen_modulo": "simulador",
                "almacen_id": almacen_id,
                "ubicacion": ubicacion,
                "departamento_id": departamento_id,
                "activo": 1,
                "empresa_id": EMPRESA_ID,
            },
        )
    ordenes = [
        ("EQ-HORNO-01", "correctivo", "alta", "abierta", "Puerta de horno no cierra correctamente"),
        ("EQ-FC-201", "correctivo", "media", "en_proceso", "Equipo hace ruido en velocidad alta"),
        ("EQ-CAM-CARNES", "preventivo", "alta", "cerrada", "Revision de temperatura y juntas"),
    ]
    for idx, (codigo, tipo, prioridad, estado, desc) in enumerate(ordenes, start=1):
        numero = f"OT-SIM-{date.today().strftime('%Y%m%d')}-{idx:03d}"
        upsert_by_keys(
            conn,
            "ordenes_trabajo",
            ["numero_orden"],
            {
                "numero_orden": numero,
                "equipo_id": equipo_ids[codigo],
                "tipo": tipo,
                "prioridad": prioridad,
                "estado": estado,
                "fecha_apertura": (datetime.now() - timedelta(days=idx)).strftime("%Y-%m-%d %H:%M:%S"),
                "descripcion": desc,
                "tecnico_asignado": "Tecnico demo",
                "horas_estimadas": 2,
                "coste_estimado": 65,
                "observaciones": "OT demo",
                "origen_modulo": "mantenimiento",
                "empresa_id": EMPRESA_ID,
                "fecha_cierre": datetime.now().strftime("%Y-%m-%d %H:%M:%S") if estado == "cerrada" else None,
            },
        )
    for codigo, tarea, frecuencia, dias in [
        ("EQ-CAM-CARNES", "Control de evaporador y juntas", "mensual", 30),
        ("EQ-LAV-01", "Limpieza filtro y dosificador", "semanal", 7),
        ("EQ-ASC-01", "Revision servicio externo", "mensual", 30),
    ]:
        upsert_by_keys(
            conn,
            "mantenimiento_preventivo",
            ["equipo_id", "tarea", "empresa_id"],
            {
                "equipo_id": equipo_ids[codigo],
                "tarea": tarea,
                "frecuencia": frecuencia,
                "intervalo_dias": dias,
                "responsable": "Mantenimiento",
                "instrucciones": "Tarea demo",
                "ultima_ejecucion": (date.today() - timedelta(days=dias + 2)).isoformat(),
                "proxima_ejecucion": (date.today() + timedelta(days=2)).isoformat(),
                "activo": 1,
                "empresa_id": EMPRESA_ID,
            },
        )
    return len(equipo_ids)


def seed_tpv(conn: sqlite3.Connection) -> int:
    pvs = conn.execute("SELECT id, nombre FROM puntos_venta WHERE empresa_id=? AND activo=1", (EMPRESA_ID,)).fetchall()
    count = 0
    if not table_exists(conn, "tpv_terminales"):
        return 0
    for pv in pvs:
        upsert_by_keys(
            conn,
            "tpv_terminales",
            ["codigo", "empresa_id"],
            {
                "codigo": f"TPV-{pv['id']:03d}",
                "nombre": f"Terminal {pv['nombre']}",
                "punto_venta_id": pv["id"],
                "tipo": "tpv",
                "activo": 1,
                "empresa_id": EMPRESA_ID,
            },
        )
        count += 1
    return count


def seed_contabilidad(conn: sqlite3.Connection, dept: dict[str, int]) -> int:
    terceros = 0
    for prov in conn.execute("SELECT * FROM proveedores WHERE empresa_id=?", (EMPRESA_ID,)):
        upsert_by_keys(
            conn,
            "contabilidad_terceros",
            ["nombre", "empresa_id"],
            {
                "tipo": "proveedor",
                "nombre": prov["nombre"],
                "nif": prov["nif"],
                "email": prov["email"],
                "telefono": prov["telefono"],
                "codigo_contable": f"400{prov['id']:04d}",
                "proveedor_id": prov["id"],
                "activo": 1,
                "empresa_id": EMPRESA_ID,
            },
        )
        terceros += 1
    docs = [
        ("gasto", "Luz y energia", 4200, dept["ADMINISTRACION"], "628000"),
        ("gasto", "Agua", 1180, dept["ADMINISTRACION"], "628001"),
        ("gasto", "Alquiler local", 7600, dept["ADMINISTRACION"], "621000"),
        ("gasto", "Comisiones OTA", 1850, dept["RECEPCION"], "626000"),
        ("ingreso", "Ventas alojamiento demo", 27800, dept["RECEPCION"], "705000"),
        ("ingreso", "Ventas bares demo", 4200, dept["BARES"], "705100"),
    ]
    for idx, (tipo, concepto, total, departamento_id, cuenta) in enumerate(docs, start=1):
        numero = f"CTB-SIM-{idx:03d}"
        if conn.execute("SELECT 1 FROM contabilidad_documentos WHERE numero=? AND empresa_id=?", (numero, EMPRESA_ID)).fetchone():
            continue
        base = dinero(total / 1.07)
        tax = dinero(total - base)
        doc_id = insert_row(
            conn,
            "contabilidad_documentos",
            {
                "tipo": tipo,
                "numero": numero,
                "fecha_documento": (date.today() - timedelta(days=idx)).isoformat(),
                "fecha_vencimiento": (date.today() + timedelta(days=30)).isoformat(),
                "estado": "pendiente_pago" if tipo == "gasto" and idx % 2 else "pagado",
                "base_imponible": base,
                "impuesto_importe": tax,
                "total": total,
                "pagado": total if idx % 2 == 0 else 0,
                "pendiente": 0 if idx % 2 == 0 else total,
                "moneda": "EUR",
                "cuenta_contable": cuenta,
                "departamento_id": departamento_id,
                "origen_modulo": "simulador",
                "observaciones": "Documento contable demo",
                "creado_por": "Simulador",
                "empresa_id": EMPRESA_ID,
            },
        )
        insert_row(
            conn,
            "contabilidad_lineas_documento",
            {
                "documento_id": doc_id,
                "concepto": concepto,
                "cantidad": 1,
                "precio_unitario": base,
                "base": base,
                "impuesto_tipo": 7,
                "impuesto_importe": tax,
                "total": total,
                "cuenta_contable": cuenta,
                "departamento_id": departamento_id,
                "empresa_id": EMPRESA_ID,
            },
        )
        if idx % 2 == 0:
            insert_row(
                conn,
                "contabilidad_pagos",
                {
                    "documento_id": doc_id,
                    "fecha_pago": date.today().isoformat(),
                    "metodo": "transferencia",
                    "importe": total,
                    "cuenta_tesoreria": "572000",
                    "referencia": f"PAY-CTB-{idx:03d}",
                    "usuario": "Simulador",
                    "empresa_id": EMPRESA_ID,
                },
            )
    return terceros + len(docs)


def report_counts(conn: sqlite3.Connection) -> dict:
    wanted = [
        "usuarios",
        "departamentos",
        "subdepartamentos",
        "habitaciones_recepcion",
        "reservas_recepcion",
        "viajeros_recepcion",
        "proveedores",
        "productos",
        "almacenes",
        "lotes",
        "pedidos",
        "recepciones",
        "menus_diarios",
        "partes_produccion",
        "equipos_mantenimiento",
        "ordenes_trabajo",
        "tpv_terminales",
        "contabilidad_documentos",
    ]
    return {
        table: conn.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"]
        for table in wanted
        if table_exists(conn, table)
    }


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    db_path = safe_db_path(args.db)
    if args.reset:
        reset_db(db_path)

    statuses = warmup_schema(db_path)
    conn = connect(db_path)
    try:
        seed_empresa(conn)
        ensure_demo_admin(conn)
        dept = seed_departments(conn)
        ensure_puntos_venta(conn, dept)
        rooms = seed_rooms(conn, args.habitaciones)
        seed_canales_y_tarifas(conn, args.dias)
        products = seed_proveedores_productos(conn, dept)
        almacenes = seed_almacenes_stock(conn, dept, products)
        reservas = seed_reservas(conn, args.dias, max(0.1, min(args.ocupacion, 0.98)))
        partes = seed_recetas_menus_produccion(conn, products, dept, args.dias)
        pedidos = seed_compras(conn, dept, products)
        equipos = seed_mantenimiento(conn, dept, almacenes)
        tpv = seed_tpv(conn)
        contabilidad = seed_contabilidad(conn, dept)
        conn.commit()
        summary = {
            "db": str(db_path),
            "login": {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            "url_sugerida": "http://127.0.0.1:5002",
            "creado": {
                "habitaciones": rooms,
                "reservas": reservas,
                "partes_produccion": partes,
                "pedidos": pedidos,
                "equipos": equipos,
                "terminales_tpv": tpv,
                "contabilidad": contabilidad,
            },
            "conteos": report_counts(conn),
            "smoke": statuses,
        }
    finally:
        conn.close()

    out_dir = ROOT / "publicaciones" / "el_raco"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "simulacion_desde_cero_resumen.json"
    report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\n[OK] Resumen guardado en: {report_path}")
    print("[OK] Para probarla sin tocar la base real:")
    print(f"     $env:ERP_DATABASE='{db_path}'")
    print("     $env:ERP_PORT='5002'")
    print("     python app.py")
    print(f"     Login: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")


if __name__ == "__main__":
    main()

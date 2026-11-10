# -*- coding: utf-8 -*-
"""Inyecta vida operativa en produccion para Hotel 1.

Crea partes reales de produccion, lineas, ingredientes consumidos, salida de
camaras, movimientos de lotes, descongelado, desinfeccion y controles APPCC.
No borra datos existentes.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3

RESPONSABLES = {
    "desayuno": ["Juan Sanchez Camacho", "Fisa Sancho Jimenez"],
    "cuarto_frio": ["Fisa Sancho Jimenez", "Marta Dorta Leon"],
    "caliente": ["Juan Sanchez Camacho", "David Molina Cruz"],
    "carnes_pescados": ["Carlos Navarro Soler", "Juan Sanchez Camacho"],
    "postres": ["Elena Castro Mora", "Fisa Sancho Jimenez"],
    "snack": ["David Molina Cruz", "Carlos Navarro Soler"],
    "buffet": ["Ana Martinez Roca", "Elena Castro Mora"],
}

KG_OBJETIVO_RACION = {
    "desayuno": 0.18,
    "carnes_pescados": 0.28,
    "caliente": 0.32,
    "cuarto_frio": 0.22,
    "postres": 0.16,
    "snack": 0.20,
    "buffet": 0.25,
}

SERVICIOS = {
    "desayuno": {"tipo": "DESAYUNO", "hora_ini": "05:30", "hora_fin": "10:30"},
    "almuerzo": {"tipo": "ALMUERZO", "hora_ini": "08:00", "hora_fin": "15:30"},
    "cena": {"tipo": "CENA", "hora_ini": "14:30", "hora_fin": "22:30"},
    "snack": {"tipo": "SNACK", "hora_ini": "10:00", "hora_fin": "18:00"},
}

SUBDEPS = {
    "desayuno": ["Desayunos", "DESAYUNOS"],
    "cuarto_frio": ["Cuarto frio", "Cuarto frío", "COCINA FRIA"],
    "caliente": ["Cocina caliente", "COCINA CALIENTE"],
    "carnes_pescados": ["Carnes y pescados", "Carniceria y Pecaderia"],
    "postres": ["Postres", "POSTRS", "PASTELERIA", "Pasteleria"],
    "snack": ["Snack"],
    "buffet": ["BUFFET", "Buffet"],
}

RECETAS = {
    "desayuno": ["Tortilla", "Huevos", "Bacon", "Pancake", "Crepes", "Fruta"],
    "cuarto_frio": ["Ensalada", "Gazpacho", "Salpicon", "Velo de pepino", "Melon", "Pasta vegetal"],
    "caliente": ["Paella", "Fideua", "Pollo", "Gulash", "Estofado", "Potage", "Pasta"],
    "carnes_pescados": ["Pollo", "Ternera", "Cerdo", "Merluza", "Pescado", "Dorado", "Langostino"],
    "postres": ["Flan", "Natillas", "Arroz con leche", "Tarta", "Crema catalana", "Mousse"],
    "snack": ["Pizza", "Nuggets", "Sandwich", "Hamburguesa", "Pasta"],
    "buffet": ["Arroz blanco", "Patata", "Verduras", "Pasta vegetal", "Coditos"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inyectar produccion operativa para Hotel 1.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--desde", default="2026-08-01")
    parser.add_argument("--dias", type=int, default=14)
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--seed", type=int, default=20260527)
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


def latest_corrida(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    for table, defs in {
        "partes_produccion": ["centro_id INTEGER", "subdepartamento_id INTEGER", "pax_previstos REAL", "regla_calculo TEXT"],
        "lineas_produccion": ["estado TEXT DEFAULT 'pendiente'", "raciones_planificadas REAL", "pax_previstos REAL", "hora_inicio TEXT", "hora_fin TEXT", "operario TEXT"],
        "lotes": ["ubicacion_actual TEXT", "fecha_descongelado DATETIME", "estado_descongelado TEXT DEFAULT 'pendiente'", "fecha_ultimo_control DATETIME", "notas_trazabilidad TEXT", "fecha_consumo DATETIME", "parte_consumo_id INTEGER"],
    }.items():
        if table_exists(conn, table):
            for definition in defs:
                add_column_if_missing(conn, table, definition)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS produccion_ingredientes_reales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            linea_produccion_id INTEGER NOT NULL,
            receta_ingrediente_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad_planificada REAL DEFAULT 0,
            cantidad_real REAL DEFAULT 0,
            observaciones TEXT,
            usuario TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(linea_produccion_id, receta_ingrediente_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS movimientos_lotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            tipo TEXT NOT NULL,
            cantidad REAL NOT NULL,
            destino_tipo TEXT,
            destino_id INTEGER,
            usuario TEXT,
            observaciones TEXT,
            documento TEXT,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registros_descongelado (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha_control DATETIME DEFAULT CURRENT_TIMESTAMP,
            temperatura REAL,
            aspecto TEXT,
            olfativo TEXT,
            decision TEXT,
            responsable TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registros_desinfeccion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            producto_usado TEXT,
            dosis TEXT,
            tiempo_contacto TEXT,
            aclarado INTEGER DEFAULT 1,
            resultado TEXT,
            responsable TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS produccion_controles_appcc (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parte_id INTEGER NOT NULL,
            linea_produccion_id INTEGER NOT NULL UNIQUE,
            receta_id INTEGER,
            responsable TEXT,
            hora_inicio_elaboracion TEXT,
            hora_fin_elaboracion TEXT,
            temperatura_fin_elaboracion REAL,
            destino_sobrante TEXT DEFAULT 'sin_sobrante',
            cantidad_sobrante REAL DEFAULT 0,
            unidad_sobrante TEXT DEFAULT 'raciones',
            motivo_merma TEXT,
            metodo_conservacion TEXT,
            temperatura_inicio_conservacion REAL,
            temperatura_fin_conservacion REAL,
            ubicacion_conservacion TEXT,
            fecha_limite_uso TEXT,
            requiere_regeneracion INTEGER DEFAULT 0,
            temperatura_servicio REAL,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            actualizado_en DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_produccion_resumen_diario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha TEXT,
            partes INTEGER DEFAULT 0,
            lineas INTEGER DEFAULT 0,
            ingredientes_reales INTEGER DEFAULT 0,
            kg_carnes REAL DEFAULT 0,
            kg_pescados REAL DEFAULT 0,
            kg_otros REAL DEFAULT 0,
            movimientos_camara INTEGER DEFAULT 0,
            controles_appcc INTEGER DEFAULT 0,
            incidencias INTEGER DEFAULT 0,
            lectura TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, fecha, centro_id)
        )
        """
    )


def norm(text: str) -> str:
    return (text or "").lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n")


def find_subdepartamento(conn: sqlite3.Connection, key: str) -> int | None:
    names = [norm(n) for n in SUBDEPS[key]]
    rows = conn.execute(
        """
        SELECT s.id, s.nombre, d.nombre dep
        FROM subdepartamentos s
        LEFT JOIN departamentos d ON d.id=s.departamento_id
        WHERE s.empresa_id=? AND COALESCE(s.activo,1)=1
          AND (s.centro_id=? OR d.centro_id=?)
        """,
        (EMPRESA_ID, CENTRO_ID, CENTRO_ID),
    ).fetchall()
    for row in rows:
        text = norm(row["nombre"])
        if any(n in text for n in names):
            return int(row["id"])
    return None


def tipo_menu(conn: sqlite3.Connection, nombre: str) -> int:
    row = conn.execute(
        """
        SELECT id FROM tipos_menu
        WHERE empresa_id=? AND centro_id=? AND UPPER(nombre)=UPPER(?)
        LIMIT 1
        """,
        (EMPRESA_ID, CENTRO_ID, nombre),
    ).fetchone()
    if row:
        return int(row["id"])
    conn.execute(
        "INSERT INTO tipos_menu (nombre, activo, empresa_id, centro_id) VALUES (?,1,?,?)",
        (nombre, EMPRESA_ID, CENTRO_ID),
    )
    return int(conn.execute("SELECT last_insert_rowid() id").fetchone()["id"])


def menu_diario(conn: sqlite3.Connection, fecha: str, servicio: str) -> int:
    tipo_id = tipo_menu(conn, SERVICIOS[servicio]["tipo"])
    row = conn.execute(
        """
        SELECT id FROM menus_diarios
        WHERE empresa_id=? AND centro_id=? AND fecha=? AND tipo_menu_id=? AND modo_servicio='simulador_produccion'
        LIMIT 1
        """,
        (EMPRESA_ID, CENTRO_ID, fecha, tipo_id),
    ).fetchone()
    if row:
        return int(row["id"])
    conn.execute(
        """
        INSERT INTO menus_diarios (fecha, tipo_menu_id, observaciones, empresa_id, modo_servicio, centro_id)
        VALUES (?, ?, ?, ?, 'simulador_produccion', ?)
        """,
        (fecha, tipo_id, f"Simulacion produccion Hotel 1 {servicio}", EMPRESA_ID, CENTRO_ID),
    )
    return int(conn.execute("SELECT last_insert_rowid() id").fetchone()["id"])


def find_almacen(conn: sqlite3.Connection, producto: sqlite3.Row) -> int | None:
    fam = norm(producto["familia"] or producto["nombre"])
    tipo = norm(producto["tipo_conservacion"] or "")
    candidates = []
    if "carne" in fam:
        candidates = ["carne", "congelados", "congel"]
    elif "pesc" in fam or "marisc" in fam:
        candidates = ["pesc", "congelados", "congel"]
    elif "verd" in fam or "frut" in fam:
        candidates = ["frutas", "verduras", "dia"]
    elif "lact" in fam or "huevo" in fam:
        candidates = ["lacteos", "desayunos"]
    elif "congel" in tipo:
        candidates = ["congelados", "congel"]
    elif "refriger" in tipo or "frio" in tipo:
        candidates = ["dia", "postres", "refriger"]
    else:
        candidates = ["economato cocina", "economato"]
    rows = conn.execute(
        """
        SELECT id, nombre
        FROM almacenes
        WHERE empresa_id=? AND COALESCE(activo,1)=1 AND (centro_id=? OR centro_id IS NULL)
        ORDER BY CASE WHEN centro_id=? THEN 0 ELSE 1 END, id DESC
        """,
        (EMPRESA_ID, CENTRO_ID, CENTRO_ID),
    ).fetchall()
    for token in candidates:
        for row in rows:
            if token in norm(row["nombre"]):
                return int(row["id"])
    return int(rows[0]["id"]) if rows else None


def recetas_para(conn: sqlite3.Connection, key: str, limit: int = 6) -> list[sqlite3.Row]:
    tokens = RECETAS[key]
    where = " OR ".join("LOWER(r.nombre) LIKE ?" for _ in tokens)
    rows = conn.execute(
        f"""
        SELECT r.id, r.nombre, COUNT(ri.id) ingredientes
        FROM recetas r
        JOIN recetas_ingredientes ri ON ri.receta_id=r.id
        WHERE r.empresa_id=? AND ({where})
        GROUP BY r.id
        HAVING ingredientes > 0
        ORDER BY r.id DESC
        LIMIT ?
        """,
        [EMPRESA_ID, *[f"%{t.lower()}%" for t in tokens], limit],
    ).fetchall()
    if rows:
        return rows
    return conn.execute(
        """
        SELECT r.id, r.nombre, COUNT(ri.id) ingredientes
        FROM recetas r JOIN recetas_ingredientes ri ON ri.receta_id=r.id
        WHERE r.empresa_id=?
        GROUP BY r.id
        HAVING ingredientes > 0
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (EMPRESA_ID, limit),
    ).fetchall()


def kpi_dia(conn: sqlite3.Connection, fecha: str) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT habitaciones_ocupadas, pax
        FROM simulador_vida_kpis_diarios
        WHERE fecha=? AND centro_id=?
        LIMIT 1
        """,
        (fecha, CENTRO_ID),
    ).fetchone()
    if not row:
        return 120, 260
    return int(row["habitaciones_ocupadas"] or 120), int(row["pax"] or 260)


def parte(conn: sqlite3.Connection, fecha: str, servicio: str, key: str, subdep_id: int | None, pax: int) -> int:
    menu_id = menu_diario(conn, fecha, servicio)
    responsable = random.choice(RESPONSABLES[key])
    row = conn.execute(
        """
        SELECT id FROM partes_produccion
        WHERE empresa_id=? AND centro_id=? AND fecha=? AND menu_diario_id=? AND COALESCE(subdepartamento_id,0)=COALESCE(?,0)
        LIMIT 1
        """,
        (EMPRESA_ID, CENTRO_ID, fecha, menu_id, subdep_id),
    ).fetchone()
    values = (
        fecha,
        f"{fecha} {SERVICIOS[servicio]['hora_ini']}:00",
        f"{fecha} {SERVICIOS[servicio]['hora_fin']}:00",
        responsable,
        "simulador_produccion",
        "completado",
        menu_id,
        SERVICIOS[servicio]["tipo"],
        EMPRESA_ID,
        subdep_id,
        pax,
        f"{key}: pax previstos {pax}",
        CENTRO_ID,
    )
    if row:
        conn.execute(
            """
            UPDATE partes_produccion
            SET hora_inicio=?, hora_fin=?, responsable=?, observaciones=?, estado=?,
                turno=?, subdepartamento_id=?, pax_previstos=?, regla_calculo=?, centro_id=?
            WHERE id=?
            """,
            values[1:4] + values[4:5] + values[5:6] + values[7:8] + values[9:] + (row["id"],),
        )
        return int(row["id"])
    conn.execute(
        """
        INSERT INTO partes_produccion
        (fecha, hora_inicio, hora_fin, responsable, observaciones, estado, menu_diario_id,
         turno, empresa_id, subdepartamento_id, pax_previstos, regla_calculo, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )
    return int(conn.execute("SELECT last_insert_rowid() id").fetchone()["id"])


def linea_produccion(
    conn: sqlite3.Connection,
    parte_id: int,
    receta_id: int,
    fecha: str,
    servicio: str,
    raciones: float,
    real_raciones: float,
    estado: str,
    pax: int,
    responsable: str,
) -> int:
    row = conn.execute(
        """
        SELECT id
        FROM lineas_produccion
        WHERE parte_id = ? AND receta_id = ? AND empresa_id = ?
        LIMIT 1
        """,
        (parte_id, receta_id, EMPRESA_ID),
    ).fetchone()
    values = (
        raciones,
        real_raciones,
        real_raciones,
        estado,
        "Simulacion operativa de produccion",
        EMPRESA_ID,
        raciones,
        pax,
        f"{fecha} {SERVICIOS[servicio]['hora_ini']}:00",
        f"{fecha} {SERVICIOS[servicio]['hora_fin']}:00",
        responsable,
    )
    if row:
        conn.execute(
            """
            UPDATE lineas_produccion
            SET cantidad_planificada = ?,
                cantidad_producida = ?,
                cantidad_real = ?,
                estado = ?,
                observaciones = ?,
                empresa_id = ?,
                raciones_planificadas = ?,
                pax_previstos = ?,
                hora_inicio = ?,
                hora_fin = ?,
                operario = ?
            WHERE id = ?
            """,
            values + (row["id"],),
        )
        return int(row["id"])
    conn.execute(
        """
        INSERT INTO lineas_produccion
        (parte_id, receta_id, cantidad_planificada, cantidad_producida, cantidad_real,
         estado, observaciones, empresa_id, raciones_planificadas, pax_previstos,
         hora_inicio, hora_fin, operario)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (parte_id, receta_id, *values),
    )
    return int(conn.execute("SELECT last_insert_rowid() id").fetchone()["id"])


def lote_para_producto(conn: sqlite3.Connection, producto: sqlite3.Row, almacen_id: int | None, fecha: str) -> int:
    codigo = f"SIM-H1-{producto['id']}-{fecha[:7].replace('-', '')}"
    row = conn.execute("SELECT id, cantidad_actual FROM lotes WHERE codigo_lote=? AND empresa_id=?", (codigo, EMPRESA_ID)).fetchone()
    if row:
        return int(row["id"])
    cantidad = random.uniform(80, 220)
    cad = (date.fromisoformat(fecha) + timedelta(days=random.randint(18, 120))).isoformat()
    temp = -18.0 if "congel" in norm(producto["tipo_conservacion"] or "") else random.uniform(1.5, 4.0)
    conn.execute(
        """
        INSERT INTO lotes
        (codigo_lote, producto_id, cantidad_inicial, cantidad_actual, fecha_recepcion,
         fecha_caducidad, almacen_id, temperatura_recepcion, estado, observaciones,
         empresa_id, responsable_recepcion, ubicacion_actual, documento_origen, notas_trazabilidad)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'disponible', ?, ?, 'Simulador produccion', ?, 'simulador_produccion', ?)
        """,
        (
            codigo,
            producto["id"],
            round(cantidad, 3),
            round(cantidad, 3),
            fecha,
            cad,
            almacen_id,
            round(temp, 1),
            "Lote creado para prueba operativa de produccion Hotel 1.",
            EMPRESA_ID,
            f"Almacen {almacen_id or '-'}",
            "Lote con trazabilidad de camara, salida y consumo.",
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid() id").fetchone()["id"])


def consumir_ingrediente(conn: sqlite3.Connection, linea_id: int, parte_id: int, ing: sqlite3.Row, cantidad: float, fecha: str, usuario: str) -> tuple[float, float, int]:
    producto = conn.execute("SELECT * FROM productos WHERE id=?", (ing["producto_id"],)).fetchone()
    if not producto:
        return 0.0, 0.0, 0
    almacen_id = find_almacen(conn, producto)
    lote_id = lote_para_producto(conn, producto, almacen_id, fecha)
    real = round(cantidad * random.uniform(0.94, 1.09), 3)
    documento = f"PROD-H1-{fecha}-{parte_id}-{linea_id}"
    conn.execute(
        """
        INSERT INTO produccion_ingredientes_reales
        (linea_produccion_id, receta_ingrediente_id, producto_id, cantidad_planificada,
         cantidad_real, observaciones, usuario, empresa_id, fecha)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(linea_produccion_id, receta_ingrediente_id) DO UPDATE SET
            producto_id=excluded.producto_id,
            cantidad_planificada=excluded.cantidad_planificada,
            cantidad_real=excluded.cantidad_real,
            observaciones=excluded.observaciones,
            usuario=excluded.usuario,
            fecha=excluded.fecha
        """,
        (linea_id, ing["id"], producto["id"], round(cantidad, 3), real, "Consumo simulado por produccion", usuario, EMPRESA_ID, f"{fecha} 12:00:00"),
    )
    conn.execute(
        """
        DELETE FROM movimientos_lotes
        WHERE empresa_id=? AND documento=? AND destino_tipo='linea_produccion' AND destino_id=?
        """,
        (EMPRESA_ID, documento, linea_id),
    )
    if table_exists(conn, "movimientos_stock"):
        conn.execute(
            """
            DELETE FROM movimientos_stock
            WHERE empresa_id=? AND documento=? AND producto_id=?
        """,
            (EMPRESA_ID, documento, producto["id"]),
        )
    conn.execute(
        """
        UPDATE lotes
        SET cantidad_actual=MAX(COALESCE(cantidad_actual,0)-?, 0),
            fecha_consumo=?, parte_consumo_id=?, ubicacion_actual=?
        WHERE id=?
        """,
        (real, f"{fecha} 12:00:00", parte_id, f"Produccion parte {parte_id}", lote_id),
    )
    conn.execute(
        """
        INSERT INTO movimientos_lotes
        (lote_id, fecha, tipo, cantidad, destino_tipo, destino_id, usuario, observaciones, documento, empresa_id)
        VALUES (?, ?, 'salida_produccion', ?, 'linea_produccion', ?, ?, ?, ?, ?)
        """,
        (lote_id, f"{fecha} 12:00:00", real, linea_id, usuario, f"Salida de camara para parte {parte_id}", documento, EMPRESA_ID),
    )
    if table_exists(conn, "movimientos_stock"):
        conn.execute(
            """
            INSERT INTO movimientos_stock
            (producto_id, almacen_id, fecha, tipo, cantidad, motivo, usuario, documento, empresa_id)
            VALUES (?, ?, ?, 'salida', ?, ?, ?, ?, ?)
            """,
            (producto["id"], almacen_id, f"{fecha} 12:00:00", real, f"Produccion parte {parte_id}", usuario, documento, EMPRESA_ID),
        )
    fam = norm(producto["familia"] or producto["nombre"])
    kg_carne = real if "carne" in fam or "pollo" in norm(producto["nombre"]) or "cerdo" in norm(producto["nombre"]) or "ternera" in norm(producto["nombre"]) else 0
    kg_pescado = real if "pesc" in fam or "marisc" in fam or any(t in norm(producto["nombre"]) for t in ["merluza", "atun", "dorado", "langost", "rodaballo"]) else 0
    if "congel" in norm(producto["tipo_conservacion"] or "") or kg_carne or kg_pescado:
        temp = random.uniform(-1.5, 3.5) if random.random() > 0.08 else random.uniform(5.5, 8.5)
        decision = "apto" if temp <= 4 else "incidencia"
        conn.execute(
            """
            INSERT INTO registros_descongelado
            (lote_id, fecha_control, temperatura, aspecto, olfativo, decision, responsable, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (lote_id, f"{fecha} 08:30:00", round(temp, 1), "correcto" if decision == "apto" else "dudoso", "normal", decision, usuario, "Control simulado de descongelado/salida de camara", EMPRESA_ID),
        )
    if any(t in fam for t in ["verd", "frut"]) or any(t in norm(producto["nombre"]) for t in ["lechuga", "tomate", "pepino"]):
        conn.execute(
            """
            INSERT INTO registros_desinfeccion
            (lote_id, fecha, producto_usado, dosis, tiempo_contacto, aclarado, resultado, responsable, observaciones, empresa_id)
            VALUES (?, ?, 'lejia alimentaria', 'segun ficha tecnica', '5 min', 1, 'correcto', ?, 'Desinfeccion simulada cuarto frio', ?)
            """,
            (lote_id, f"{fecha} 09:00:00", usuario, EMPRESA_ID),
        )
    return kg_carne, kg_pescado, 1


def log_event(conn: sqlite3.Connection, corrida_id: int, fecha: str, tipo: str, severidad: str, descripcion: str, registro_id: int | None = None) -> None:
    if not table_exists(conn, "simulador_vida_eventos"):
        return
    conn.execute(
        """
        INSERT INTO simulador_vida_eventos
        (corrida_id, fecha_simulada, modulo, tipo, severidad, descripcion, tabla_ref, registro_id, impacto_json, empresa_id, centro_id)
        VALUES (?, ?, 'produccion', ?, ?, ?, 'partes_produccion', ?, '{}', ?, ?)
        """,
        (corrida_id, fecha, tipo, severidad, descripcion, registro_id, EMPRESA_ID, CENTRO_ID),
    )


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida de simulacion.")
        start = date.fromisoformat(args.desde)
        total_partes = total_lineas = total_ingredientes = 0
        for offset in range(args.dias):
            day = start + timedelta(days=offset)
            fecha = day.isoformat()
            _, pax_base = kpi_dia(conn, fecha)
            resumen = {"partes": 0, "lineas": 0, "ingredientes": 0, "kg_carnes": 0.0, "kg_pescados": 0.0, "kg_otros": 0.0, "movimientos": 0, "controles": 0, "incidencias": 0}
            plan = [
                ("desayuno", "desayuno", int(pax_base * 0.72), 3),
                ("almuerzo", "carnes_pescados", int(pax_base * 0.58), 2),
                ("almuerzo", "caliente", int(pax_base * 0.62), 4),
                ("almuerzo", "cuarto_frio", int(pax_base * 0.52), 3),
                ("almuerzo", "postres", int(pax_base * 0.46), 2),
                ("cena", "carnes_pescados", int(pax_base * 0.66), 2),
                ("cena", "caliente", int(pax_base * 0.70), 4),
                ("cena", "cuarto_frio", int(pax_base * 0.60), 3),
                ("cena", "postres", int(pax_base * 0.55), 2),
                ("snack", "snack", int(pax_base * 0.18), 2),
                ("almuerzo", "buffet", int(pax_base * 0.62), 2),
            ]
            for servicio, key, pax, nrecetas in plan:
                subdep_id = find_subdepartamento(conn, key)
                parte_id = parte(conn, fecha, servicio, key, subdep_id, pax)
                resumen["partes"] += 1
                total_partes += 1
                recetas = recetas_para(conn, key, nrecetas)
                responsable = random.choice(RESPONSABLES[key])
                for receta in recetas:
                    raciones = max(12, round(pax / max(len(recetas), 1) * random.uniform(0.85, 1.12), 1))
                    real_raciones = round(raciones * random.uniform(0.94, 1.08), 1)
                    estado = "completado" if random.random() > 0.09 else "incidencia"
                    linea_id = linea_produccion(
                        conn,
                        parte_id,
                        receta["id"],
                        fecha,
                        servicio,
                        raciones,
                        real_raciones,
                        estado,
                        pax,
                        responsable,
                    )
                    resumen["lineas"] += 1
                    total_lineas += 1
                    ingredientes = conn.execute(
                        """
                        SELECT ri.*, p.nombre, p.familia, p.tipo_conservacion
                        FROM recetas_ingredientes ri
                        JOIN productos p ON p.id=ri.producto_id
                        WHERE ri.receta_id=?
                        LIMIT 8
                        """,
                        (receta["id"],),
                    ).fetchall()
                    consumos_planificados = []
                    for ing in ingredientes:
                        base = float(ing["cantidad"] or 0) * max(real_raciones, 1)
                        if base <= 0:
                            base = random.uniform(0.2, 2.5)
                        consumos_planificados.append((ing, base))
                    bruto_linea = sum(base for _, base in consumos_planificados)
                    objetivo_kg_racion = KG_OBJETIVO_RACION.get(key, 0.24)
                    techo_linea = max(0.8, real_raciones * objetivo_kg_racion * random.uniform(0.86, 1.14))
                    escala = min(1.0, techo_linea / bruto_linea) if bruto_linea > 0 else 1.0
                    for ing, base in consumos_planificados:
                        cantidad = round(max(base * escala, 0.02), 3)
                        kg_carne, kg_pescado, mov = consumir_ingrediente(conn, linea_id, parte_id, ing, cantidad, fecha, responsable)
                        resumen["kg_carnes"] += kg_carne
                        resumen["kg_pescados"] += kg_pescado
                        resumen["kg_otros"] += max(cantidad - kg_carne - kg_pescado, 0)
                        resumen["movimientos"] += mov
                        resumen["ingredientes"] += 1
                        total_ingredientes += 1
                    temp_fin = random.uniform(68, 82) if key in {"caliente", "carnes_pescados", "snack"} else random.uniform(2, 7)
                    incidencia = temp_fin < 65 if key in {"caliente", "carnes_pescados"} else temp_fin > 6.5
                    sobrante = round(real_raciones * random.uniform(0.03, 0.18), 1)
                    destino = "reaprovechamiento" if sobrante > 4 and not incidencia and key in {"caliente", "cuarto_frio", "postres"} else "merma_buffet" if sobrante > 0 else "sin_sobrante"
                    conn.execute(
                        """
                        INSERT INTO produccion_controles_appcc
                        (parte_id, linea_produccion_id, receta_id, responsable, hora_inicio_elaboracion,
                         hora_fin_elaboracion, temperatura_fin_elaboracion, destino_sobrante,
                         cantidad_sobrante, unidad_sobrante, motivo_merma, metodo_conservacion,
                         temperatura_inicio_conservacion, temperatura_fin_conservacion,
                         ubicacion_conservacion, fecha_limite_uso, requiere_regeneracion,
                         temperatura_servicio, observaciones, empresa_id, actualizado_en)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'raciones', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(linea_produccion_id) DO UPDATE SET
                            responsable=excluded.responsable,
                            temperatura_fin_elaboracion=excluded.temperatura_fin_elaboracion,
                            destino_sobrante=excluded.destino_sobrante,
                            cantidad_sobrante=excluded.cantidad_sobrante,
                            motivo_merma=excluded.motivo_merma,
                            observaciones=excluded.observaciones,
                            actualizado_en=excluded.actualizado_en
                        """,
                        (
                            parte_id,
                            linea_id,
                            receta["id"],
                            responsable,
                            f"{fecha} {SERVICIOS[servicio]['hora_ini']}:00",
                            f"{fecha} {SERVICIOS[servicio]['hora_fin']}:00",
                            round(temp_fin, 1),
                            destino,
                            sobrante,
                            "Sobrante buffet simulado" if destino != "sin_sobrante" else "",
                            "frio" if destino == "reaprovechamiento" else "servicio_directo",
                            round(random.uniform(2, 5), 1),
                            round(random.uniform(2, 5), 1),
                            "Camara dia" if destino == "reaprovechamiento" else "",
                            (day + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
                            1 if destino == "reaprovechamiento" else 0,
                            round(temp_fin, 1),
                            "Control APPCC simulado: revisar si temperatura o sobrante no cuadra." if incidencia else "Control APPCC simulado correcto.",
                            EMPRESA_ID,
                            f"{fecha} 23:00:00",
                        ),
                    )
                    resumen["controles"] += 1
                    if incidencia or estado == "incidencia":
                        resumen["incidencias"] += 1
                        log_event(conn, corrida_id, fecha, "incidencia_produccion", "alta", f"Incidencia simulada en {receta['nombre']} ({key}). Revisar temperatura, merma o salida de camara.", parte_id)
            lectura = "Produccion completa con trazabilidad de camaras."
            if resumen["incidencias"]:
                lectura = "Produccion con incidencias: revisar APPCC, temperaturas, mermas o salida de camara."
            conn.execute(
                """
                INSERT INTO simulador_produccion_resumen_diario
                (corrida_id, fecha, partes, lineas, ingredientes_reales, kg_carnes, kg_pescados,
                 kg_otros, movimientos_camara, controles_appcc, incidencias, lectura, empresa_id, centro_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(corrida_id, fecha, centro_id) DO UPDATE SET
                    partes=excluded.partes,
                    lineas=excluded.lineas,
                    ingredientes_reales=excluded.ingredientes_reales,
                    kg_carnes=excluded.kg_carnes,
                    kg_pescados=excluded.kg_pescados,
                    kg_otros=excluded.kg_otros,
                    movimientos_camara=excluded.movimientos_camara,
                    controles_appcc=excluded.controles_appcc,
                    incidencias=excluded.incidencias,
                    lectura=excluded.lectura
                """,
                (
                    corrida_id,
                    fecha,
                    resumen["partes"],
                    resumen["lineas"],
                    resumen["ingredientes"],
                    round(resumen["kg_carnes"], 2),
                    round(resumen["kg_pescados"], 2),
                    round(resumen["kg_otros"], 2),
                    resumen["movimientos"],
                    resumen["controles"],
                    resumen["incidencias"],
                    lectura,
                    EMPRESA_ID,
                    CENTRO_ID,
                ),
            )
        conn.commit()
        print(f"Produccion simulada: {args.dias} dias, {total_partes} partes, {total_lineas} lineas, {total_ingredientes} ingredientes reales.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

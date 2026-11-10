# -*- coding: utf-8 -*-
"""Convierte la prueba de fuego del simulador en aprendizaje para la IA.

No decide por el usuario. Registra lecciones, crea investigaciones externas
pendientes y abre un backlog tecnico/operativo para mejorar toda la ERP/PMS.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


MODULO_POR_AREA = {
    "recetas": "Cocina",
    "menus": "Cocina",
    "produccion": "Produccion",
    "cocina": "Cocina",
    "compras": "Compras",
    "stock": "Compras",
    "calendario": "Operaciones",
    "appcc": "APPCC",
    "rrhh": "RRHH",
    "mantenimiento": "Mantenimiento",
}


INVESTIGACIONES_EXTERNAS = {
    "menu_sin_alergenos": {
        "consulta": "normativa alergenos hosteleria espana etiquetas buffet restaurante",
        "objetivo": "Contrastar obligaciones y buenas practicas para alergenos visibles en buffet/menu.",
        "area": "cocina",
    },
    "recetas_sin_ingredientes": {
        "consulta": "fichas tecnicas recetas escandallo cocina colectiva hotel buffet buenas practicas",
        "objetivo": "Buscar estructura de ficha tecnica y escandallo para recetas de produccion hotelera.",
        "area": "cocina",
    },
    "escandallos_alto_riesgo": {
        "consulta": "control coste receta cocina hotel buffet food cost desviaciones merma",
        "objetivo": "Contrastar rangos y controles habituales de food cost y desviacion por plato.",
        "area": "finanzas",
    },
    "merma_valorada": {
        "consulta": "reducir desperdicio alimentario buffet hotel merma reaprovechamiento appcc",
        "objetivo": "Buscar medidas operativas contra merma en buffet sin comprometer seguridad alimentaria.",
        "area": "operaciones",
    },
    "pedidos_sin_recepcion": {
        "consulta": "mejores practicas recepcion mercancias hosteleria pedido albaran incidencias trazabilidad",
        "objetivo": "Contrastar flujo correcto de pedido, recepcion, albaran, incidencia y cierre.",
        "area": "compras",
    },
    "productos_bajo_minimo": {
        "consulta": "gestion stock minimo hosteleria compras proveedor rotura stock cocina hotel",
        "objetivo": "Contrastar reglas de stock minimo y compra preventiva para hoteles/restaurantes.",
        "area": "compras",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aprender de la ultima prueba de fuego del simulador.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_column_if_missing(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.strip().split()[0]
    if name not in columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_memoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            tipo TEXT NOT NULL,
            modulo TEXT NOT NULL,
            clave TEXT NOT NULL,
            valor TEXT,
            confianza REAL DEFAULT 0.5,
            origen TEXT DEFAULT 'manual',
            detalle TEXT,
            estado TEXT DEFAULT 'activa',
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_investigaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            modulo TEXT DEFAULT 'IA Estrategica',
            tipo TEXT DEFAULT 'investigacion_externa',
            objetivo TEXT,
            consulta TEXT,
            perfil_cliente TEXT,
            restricciones TEXT,
            idioma TEXT,
            fuente_titulo TEXT,
            fuente_url TEXT,
            resumen TEXT,
            propuesta TEXT,
            riesgos TEXT,
            estado TEXT DEFAULT 'pendiente_revision',
            recomendacion_id INTEGER,
            usuario TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_mejoras_erp_backlog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            area TEXT NOT NULL,
            modulo TEXT NOT NULL,
            codigo TEXT NOT NULL,
            nivel TEXT NOT NULL,
            titulo TEXT NOT NULL,
            problema TEXT,
            solucion_propuesta TEXT,
            aplica_a_erp INTEGER DEFAULT 1,
            requiere_contraste_externo INTEGER DEFAULT 0,
            estado TEXT DEFAULT 'pendiente',
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    add_column_if_missing(conn, "asistente_memoria", "fecha_actualizacion DATETIME")
    add_column_if_missing(conn, "simulador_mejoras_erp_backlog", "requiere_contraste_externo INTEGER DEFAULT 0")


def latest_corrida(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone() if table_exists(conn, "simulador_vida_corridas") else None
    return int(row["id"]) if row else 0


def confianza_por_nivel(nivel: str) -> float:
    return {"critica": 0.88, "alta": 0.78, "media": 0.62}.get(nivel, 0.5)


def guardar_memoria(conn: sqlite3.Connection, issue: sqlite3.Row, corrida_id: int, centro_id: int) -> bool:
    modulo = MODULO_POR_AREA.get(issue["area"], "Operaciones")
    clave = f"simulador:{centro_id}:{issue['codigo']}"
    existente = conn.execute(
        """
        SELECT id FROM asistente_memoria
        WHERE empresa_id=? AND origen='simulador_prueba_fuego' AND clave=?
        LIMIT 1
        """,
        (EMPRESA_ID, clave),
    ).fetchone()
    detalle = (
        f"Hotel 1 / corrida {corrida_id}. Nivel {issue['nivel']}. "
        f"Valor {issue['valor']} sobre referencia {issue['referencia']}. "
        f"Problema: {issue['detalle'] or issue['titulo']}. "
        f"Accion propuesta: {issue['accion'] or 'revisar operativa'}."
    )
    if existente:
        conn.execute(
            """
            UPDATE asistente_memoria
            SET modulo=?, valor=?, confianza=?, detalle=?, estado='activa',
                fecha_actualizacion=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (modulo, issue["nivel"], confianza_por_nivel(issue["nivel"]), detalle, existente["id"]),
        )
        return False
    conn.execute(
        """
        INSERT INTO asistente_memoria
        (empresa_id, tipo, modulo, clave, valor, confianza, origen, detalle, estado)
        VALUES (?, 'aprendizaje_simulador', ?, ?, ?, ?, 'simulador_prueba_fuego', ?, 'activa')
        """,
        (EMPRESA_ID, modulo, clave, issue["nivel"], confianza_por_nivel(issue["nivel"]), detalle),
    )
    return True


def guardar_investigacion(conn: sqlite3.Connection, issue: sqlite3.Row) -> bool:
    spec = INVESTIGACIONES_EXTERNAS.get(issue["codigo"])
    if not spec:
        return False
    consulta = spec["consulta"]
    existente = conn.execute(
        """
        SELECT id FROM asistente_investigaciones
        WHERE empresa_id=? AND consulta=? AND estado IN ('pendiente_revision','pendiente')
        LIMIT 1
        """,
        (EMPRESA_ID, consulta),
    ).fetchone()
    if existente:
        return False
    conn.execute(
        """
        INSERT INTO asistente_investigaciones
        (empresa_id, modulo, tipo, objetivo, consulta, perfil_cliente, restricciones,
         idioma, resumen, propuesta, riesgos, estado, usuario)
        VALUES (?, ?, 'investigacion_externa_simulador', ?, ?, 'Hotel 1 / hosteleria',
                'Contrastar con fuentes externas antes de convertir en recomendacion fuerte.',
                'es', ?, ?, ?, 'pendiente_revision', 'simulador')
        """,
        (
            EMPRESA_ID,
            MODULO_POR_AREA.get(issue["area"], "IA Estrategica"),
            spec["objetivo"],
            consulta,
            f"Disparado por simulador: {issue['titulo']} ({issue['valor']}).",
            issue["accion"] or "Buscar buenas practicas y adaptar a El Raco.",
            "No usar como verdad hasta revisar fuente, fecha y encaje con el modelo de negocio.",
        ),
    )
    return True


def guardar_backlog(conn: sqlite3.Connection, issue: sqlite3.Row, corrida_id: int, centro_id: int) -> bool:
    modulo = MODULO_POR_AREA.get(issue["area"], "Operaciones")
    requiere_externo = 1 if issue["codigo"] in INVESTIGACIONES_EXTERNAS else 0
    existente = conn.execute(
        """
        SELECT id FROM simulador_mejoras_erp_backlog
        WHERE empresa_id=? AND centro_id=? AND codigo=? AND estado IN ('pendiente','en_revision')
        LIMIT 1
        """,
        (EMPRESA_ID, centro_id, issue["codigo"]),
    ).fetchone()
    if existente:
        conn.execute(
            """
            UPDATE simulador_mejoras_erp_backlog
            SET corrida_id=?, nivel=?, titulo=?, problema=?, solucion_propuesta=?,
                requiere_contraste_externo=?, created_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                corrida_id,
                issue["nivel"],
                issue["titulo"],
                issue["detalle"],
                issue["accion"],
                requiere_externo,
                existente["id"],
            ),
        )
        return False
    conn.execute(
        """
        INSERT INTO simulador_mejoras_erp_backlog
        (corrida_id, centro_id, area, modulo, codigo, nivel, titulo, problema,
         solucion_propuesta, aplica_a_erp, requiere_contraste_externo, estado, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'pendiente', ?)
        """,
        (
            corrida_id,
            centro_id,
            issue["area"],
            modulo,
            issue["codigo"],
            issue["nivel"],
            issue["titulo"],
            issue["detalle"],
            issue["accion"],
            requiere_externo,
            EMPRESA_ID,
        ),
    )
    return True


def aprender(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    if not table_exists(conn, "simulador_prueba_fuego_detalle"):
        return {"ok": False, "motivo": "No existe simulador_prueba_fuego_detalle"}
    rows = conn.execute(
        """
        SELECT *
        FROM simulador_prueba_fuego_detalle
        WHERE corrida_id=? AND centro_id=?
        ORDER BY CASE nivel WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 ELSE 2 END, valor DESC
        """,
        (corrida_id, centro_id),
    ).fetchall()
    nuevas_memorias = 0
    nuevas_investigaciones = 0
    nuevo_backlog = 0
    for issue in rows:
        if guardar_memoria(conn, issue, corrida_id, centro_id):
            nuevas_memorias += 1
        if guardar_investigacion(conn, issue):
            nuevas_investigaciones += 1
        if guardar_backlog(conn, issue, corrida_id, centro_id):
            nuevo_backlog += 1
    conn.commit()
    return {
        "ok": True,
        "corrida_id": corrida_id,
        "centro_id": centro_id,
        "issues_leidas": len(rows),
        "memorias_nuevas": nuevas_memorias,
        "investigaciones_nuevas": nuevas_investigaciones,
        "backlog_nuevo": nuevo_backlog,
    }


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    if not db.is_absolute():
        db = ROOT / db
    conn = connect(db)
    try:
        ensure_schema(conn)
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida de simulacion.")
        resultado = aprender(conn, corrida_id, args.centro_id)
    finally:
        conn.close()
    print(json.dumps(resultado, ensure_ascii=False, indent=2) if args.json else resultado)


if __name__ == "__main__":
    main()

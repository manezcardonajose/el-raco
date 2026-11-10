# -*- coding: utf-8 -*-
"""Alimenta la IA de la ERP con resultados de simuladores por centro.

Convierte auditorias y pruebas de fuego en:
- memoria interna trazable;
- recomendaciones pendientes;
- eventos de aprendizaje.

La IA aprende con prudencia: lo simulado no se marca como verdad, sino como
senal operativa con confianza limitada.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
DEFAULT_HOTEL1_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aprender de simuladores hacia IA de la ERP.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--hotel1-db", default=str(DEFAULT_HOTEL1_DB))
    parser.add_argument("--incluir-hotel1", action="store_true")
    parser.add_argument(
        "--incluir-hotel1-main",
        action="store_true",
        help="Permite aprender del Hotel 1 de pepe4.db. Por defecto se omite si se usa --incluir-hotel1.",
    )
    parser.add_argument(
        "--limpiar-ruido-hotel1-main",
        action="store_true",
        help="Elimina memoria/recomendaciones/eventos del Hotel 1 de pepe4.db generados por prueba_fuego_centro.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_memoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            tipo TEXT NOT NULL,
            modulo TEXT,
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
        CREATE TABLE IF NOT EXISTS asistente_recomendaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            empresa_id INTEGER DEFAULT 1,
            modulo TEXT NOT NULL,
            categoria TEXT,
            prioridad TEXT DEFAULT 'media',
            titulo TEXT NOT NULL,
            detalle TEXT,
            datos_usados TEXT,
            accion_propuesta TEXT,
            estado TEXT DEFAULT 'pendiente',
            aceptada_por TEXT,
            fecha_decision DATETIME,
            resultado TEXT,
            origen TEXT DEFAULT 'reglas_base'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_eventos_aprendizaje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            recomendacion_id INTEGER,
            tipo TEXT NOT NULL,
            modulo TEXT,
            senal TEXT,
            decision TEXT,
            resultado TEXT,
            usuario TEXT,
            fecha_evento DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def upsert_memoria(conn: sqlite3.Connection, empresa_id: int, modulo: str, clave: str, valor: str, detalle: str, confianza: float, origen: str) -> str:
    row = conn.execute(
        """
        SELECT id FROM asistente_memoria
        WHERE empresa_id=? AND clave=? AND origen=?
        LIMIT 1
        """,
        (empresa_id, clave, origen),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE asistente_memoria
            SET tipo='aprendizaje_simulador', modulo=?, valor=?, confianza=?,
                detalle=?, estado='activa', fecha_actualizacion=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (modulo, valor, confianza, detalle, row["id"]),
        )
        return "actualizada"
    conn.execute(
        """
        INSERT INTO asistente_memoria
        (empresa_id, tipo, modulo, clave, valor, confianza, origen, detalle, estado)
        VALUES (?, 'aprendizaje_simulador', ?, ?, ?, ?, ?, ?, 'activa')
        """,
        (empresa_id, modulo, clave, valor, confianza, origen, detalle),
    )
    return "creada"


def crear_recomendacion(conn: sqlite3.Connection, empresa_id: int, modulo: str, categoria: str, prioridad: str, titulo: str, detalle: str, accion: str, datos: dict, origen: str) -> bool:
    row = conn.execute(
        """
        SELECT id FROM asistente_recomendaciones
        WHERE empresa_id=? AND modulo=? AND titulo=? AND origen=?
          AND estado IN ('pendiente','revisada')
        LIMIT 1
        """,
        (empresa_id, modulo, titulo, origen),
    ).fetchone()
    if row:
        return False
    conn.execute(
        """
        INSERT INTO asistente_recomendaciones
        (empresa_id, modulo, categoria, prioridad, titulo, detalle, datos_usados,
         accion_propuesta, estado, origen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', ?)
        """,
        (empresa_id, modulo, categoria, prioridad, titulo, detalle, json.dumps(datos, ensure_ascii=False), accion, origen),
    )
    return True


def evento(conn: sqlite3.Connection, empresa_id: int, modulo: str, senal: str, decision: str, resultado: str) -> bool:
    row = conn.execute(
        """
        SELECT id FROM asistente_eventos_aprendizaje
        WHERE empresa_id=? AND tipo='aprendizaje_simulador'
          AND modulo=? AND senal=? AND decision=?
        LIMIT 1
        """,
        (empresa_id, modulo, senal, decision),
    ).fetchone()
    if row:
        return False
    conn.execute(
        """
        INSERT INTO asistente_eventos_aprendizaje
        (empresa_id, tipo, modulo, senal, decision, resultado, usuario)
        VALUES (?, 'aprendizaje_simulador', ?, ?, ?, ?, 'simulador')
        """,
        (empresa_id, modulo, senal, decision, resultado),
    )
    return True


def prioridad_desde_riesgo(nivel: str) -> str:
    nivel = (nivel or "").lower()
    if nivel == "critica":
        return "critica"
    if nivel == "alta":
        return "alta"
    if nivel in {"media", "vigilar"}:
        return "media"
    return "baja"


def modulo_desde_area(area: str) -> str:
    base = (area or "").lower()
    if any(x in base for x in ("pms", "reserva", "viajero")):
        return "Recepcion"
    if any(x in base for x in ("compra", "stock", "proveedor")):
        return "Compras"
    if any(x in base for x in ("cocina", "produccion", "receta")):
        return "Cocina"
    if any(x in base for x in ("finanza", "contabilidad", "presupuesto")):
        return "Finanzas"
    if any(x in base for x in ("rrhh", "equipo", "personal")):
        return "RRHH"
    if any(x in base for x in ("mantenimiento", "appcc")):
        return "Mantenimiento"
    return "Direccion"


def latest_prueba_centros(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not table_exists(conn, "simulador_prueba_fuego_centros"):
        return []
    fechas = conn.execute(
        """
        SELECT empresa_id, centro_id, MAX(fecha) AS fecha
        FROM simulador_prueba_fuego_centros
        GROUP BY empresa_id, centro_id
        """
    ).fetchall()
    rows = []
    for f in fechas:
        row = conn.execute(
            """
            SELECT * FROM simulador_prueba_fuego_centros
            WHERE empresa_id=? AND centro_id=? AND fecha=?
            ORDER BY id DESC LIMIT 1
            """,
            (f["empresa_id"], f["centro_id"], f["fecha"]),
        ).fetchone()
        if row:
            rows.append(row)
    return rows


def es_hotel1_main(row: sqlite3.Row, data: dict) -> bool:
    datos = data.get("datos") or {}
    centro = (datos.get("centro") or "").strip().lower()
    return int(row["empresa_id"] or 0) == 1 and int(row["centro_id"] or 0) == 3 and centro in {"hotel 1", "hotel_1"}


def limpiar_ruido_hotel1_main(conn: sqlite3.Connection) -> dict:
    """Quita aprendizaje del Hotel 1 de la base principal, manteniendo el laboratorio."""
    memoria = conn.execute(
        """
        DELETE FROM asistente_memoria
        WHERE empresa_id=1
          AND origen='simulador_prueba_fuego_centro'
          AND clave LIKE 'centro:3:prueba_fuego:%'
        """
    ).rowcount
    recomendaciones = conn.execute(
        """
        DELETE FROM asistente_recomendaciones
        WHERE empresa_id=1
          AND origen='simulador_prueba_fuego_centro'
          AND titulo LIKE 'Hotel 1:%'
        """
    ).rowcount
    eventos = conn.execute(
        """
        DELETE FROM asistente_eventos_aprendizaje
        WHERE empresa_id=1
          AND tipo='aprendizaje_simulador'
          AND senal='Prueba fuego Hotel 1'
        """
    ).rowcount
    return {"memoria": memoria, "recomendaciones": recomendaciones, "eventos": eventos}


def aprender_prueba_centros(conn: sqlite3.Connection, omitir_hotel1_main: bool = False) -> dict:
    creadas = actualizadas = recomendaciones = eventos = 0
    omitidas = 0
    for row in latest_prueba_centros(conn):
        data = json.loads(row["resumen_json"] or "{}")
        if omitir_hotel1_main and es_hotel1_main(row, data):
            omitidas += 1
            continue
        datos = data.get("datos") or {}
        centro = datos.get("centro") or f"Centro {row['centro_id']}"
        empresa_id = int(row["empresa_id"])
        centro_id = int(row["centro_id"])
        score = int(row["score"] or 0)
        estado = row["estado"] or "sin_estado"
        p = datos.get("presupuesto") or {}
        clave = f"centro:{centro_id}:prueba_fuego:{datos.get('year')}"
        detalle = (
            f"{centro}: prueba de fuego {estado}, score {score}/100. "
            f"Reservas {datos.get('reservas', 0)}, pedidos {datos.get('pedidos', 0)}, "
            f"produccion {datos.get('partes_produccion', 0)}/{datos.get('lineas_produccion', 0)}. "
            f"Resultado presupuesto {p.get('resultado', 0)} EUR."
        )
        res = upsert_memoria(conn, empresa_id, "Direccion", clave, estado, detalle, min(0.82, max(0.35, score / 100)), "simulador_prueba_fuego_centro")
        creadas += res == "creada"
        actualizadas += res == "actualizada"
        if evento(conn, empresa_id, "Direccion", f"Prueba fuego {centro}", "aprender_estado_centro", detalle):
            eventos += 1

        for check in data.get("checks") or []:
            if check.get("estado") == "ok":
                continue
            modulo = modulo_desde_area(check.get("nombre"))
            titulo = f"{centro}: falta {check.get('nombre')}"
            accion = check.get("accion") or "Completar datos antes de decisiones fuertes."
            if crear_recomendacion(conn, empresa_id, modulo, "prueba_fuego", "alta", titulo, detalle, accion, {"centro_id": centro_id, "check": check}, "simulador_prueba_fuego_centro"):
                recomendaciones += 1

        for riesgo in data.get("riesgos") or []:
            modulo = modulo_desde_area(riesgo.get("area"))
            titulo = f"{centro}: {riesgo.get('area')} - {riesgo.get('nivel')}"
            accion = "Corregir antes de usar la simulacion como decision de negocio."
            if crear_recomendacion(conn, empresa_id, modulo, "riesgo_simulador", prioridad_desde_riesgo(riesgo.get("nivel")), titulo, riesgo.get("texto") or detalle, accion, {"centro_id": centro_id, "riesgo": riesgo}, "simulador_prueba_fuego_centro"):
                recomendaciones += 1
    return {
        "memoria_creada": creadas,
        "memoria_actualizada": actualizadas,
        "recomendaciones": recomendaciones,
        "eventos": eventos,
        "omitidas": omitidas,
    }


def hotel1_veredicto(hotel1_db: Path) -> dict | None:
    if not hotel1_db.exists():
        return None
    conn = connect(hotel1_db)
    try:
        if not table_exists(conn, "simulador_veredicto_brutal"):
            return None
        row = conn.execute("SELECT * FROM simulador_veredicto_brutal ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        data = dict(row)
        for key in ("fortalezas_json", "roturas_json", "acciones_json"):
            if key in data:
                try:
                    data[key.replace("_json", "")] = json.loads(data[key] or "[]")
                except Exception:
                    data[key.replace("_json", "")] = []
        return data
    finally:
        conn.close()


def hotel1_prueba_fuego(hotel1_db: Path) -> dict | None:
    if not hotel1_db.exists():
        return None
    conn = connect(hotel1_db)
    try:
        if not table_exists(conn, "simulador_prueba_fuego_centros"):
            return None
        row = conn.execute("SELECT * FROM simulador_prueba_fuego_centros ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["resumen"] = json.loads(data.get("resumen_json") or "{}")
        except Exception:
            data["resumen"] = {}
        return data
    finally:
        conn.close()


def hotel1_planes_decision(hotel1_db: Path) -> list[dict]:
    if not hotel1_db.exists():
        return []
    conn = connect(hotel1_db)
    try:
        if not table_exists(conn, "simulador_plan_decision_mensual"):
            return []
        rows = conn.execute(
            """
            SELECT *
            FROM simulador_plan_decision_mensual
            WHERE corrida_id=(SELECT MAX(corrida_id) FROM simulador_plan_decision_mensual)
              AND centro_id=3
            ORDER BY mes
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def aprender_hotel1(conn: sqlite3.Connection, hotel1_db: Path) -> dict:
    data = hotel1_veredicto(hotel1_db) or {}
    prueba = hotel1_prueba_fuego(hotel1_db) or {}
    if not data and not prueba:
        return {"omitido": "sin_datos_hotel1"}
    empresa_id = 1
    nota = int(prueba.get("score") or data.get("nota") or 0)
    nivel = prueba.get("estado") or data.get("nivel") or "sin_nivel"
    resumen = prueba.get("resumen") or {}
    datos = resumen.get("datos") or {}
    riesgos = resumen.get("riesgos") or []
    checks_malos = [c for c in resumen.get("checks") or [] if c.get("estado") != "ok"]
    detalle = (
        f"Hotel 1 laboratorio: {data.get('titular') or 'prueba de fuego'}. "
        f"Estado actual {nivel}, score actual {nota}/100. "
        f"Veredicto anterior {data.get('nota', 's/d')}/100, nivel anterior {data.get('nivel', 's/d')}. "
        f"Ultima prueba: score {prueba.get('score', 's/d')}/100, estado {prueba.get('estado', 's/d')}, "
        f"reservas {datos.get('reservas', 's/d')}, pedidos {datos.get('pedidos', 's/d')}, "
        f"produccion {datos.get('partes_produccion', 's/d')}/{datos.get('lineas_produccion', 's/d')}, "
        f"riesgos {len(riesgos)}, checks malos {len(checks_malos)}. "
        f"{data.get('conclusion') or data.get('veredicto') or ''}"
    )
    confianza_base = prueba.get("score") or nota
    res = upsert_memoria(conn, empresa_id, "Direccion", "hotel1:laboratorio:veredicto_brutal", nivel, detalle, min(0.86, max(0.35, confianza_base / 100)), "simulador_hotel1_laboratorio")
    recs = 0
    eventos = 0
    if evento(conn, empresa_id, "Direccion", "Hotel 1 veredicto brutal", "aprender_laboratorio", detalle):
        eventos += 1
    for rotura in data.get("roturas") or []:
        modulo = modulo_desde_area(rotura.get("area"))
        titulo = f"Hotel 1 laboratorio: {rotura.get('area')}"
        if crear_recomendacion(
            conn,
            empresa_id,
            modulo,
            "laboratorio_hotel1",
            prioridad_desde_riesgo(rotura.get("nivel")),
            titulo,
            rotura.get("texto") or detalle,
            "Pasar esta rotura al backlog de la ERP/PMS si tambien puede afectar a centros reales.",
            {"rotura": rotura, "veredicto_id": data.get("id")},
            "simulador_hotel1_laboratorio",
        ):
            recs += 1
    for check in checks_malos:
        modulo = modulo_desde_area(check.get("nombre"))
        titulo = f"Hotel 1 laboratorio prueba fuego: falta {check.get('nombre')}"
        if crear_recomendacion(
            conn,
            empresa_id,
            modulo,
            "laboratorio_prueba_fuego",
            "alta",
            titulo,
            detalle,
            check.get("accion") or "Completar el modulo antes de confiar en resultados.",
            {"check": check, "prueba_fuego_id": prueba.get("id")},
            "simulador_hotel1_laboratorio",
        ):
            recs += 1
    for riesgo in riesgos:
        modulo = modulo_desde_area(riesgo.get("area"))
        titulo = f"Hotel 1 laboratorio prueba fuego: {riesgo.get('area')} - {riesgo.get('nivel')}"
        if crear_recomendacion(
            conn,
            empresa_id,
            modulo,
            "laboratorio_prueba_fuego",
            prioridad_desde_riesgo(riesgo.get("nivel")),
            titulo,
            riesgo.get("texto") or detalle,
            "Corregir esta rotura del laboratorio y comprobar si puede afectar a centros reales.",
            {"riesgo": riesgo, "prueba_fuego_id": prueba.get("id")},
            "simulador_hotel1_laboratorio",
        ):
            recs += 1
    planes = hotel1_planes_decision(hotel1_db)
    for plan in planes:
        if plan.get("veredicto") == "mantener_control":
            continue
        prioridad = "critica" if plan.get("veredicto") == "mejora_no_basta" else "alta"
        titulo = f"Hotel 1 laboratorio decision {plan.get('mes')}: {plan.get('veredicto')}"
        detalle_plan = (
            f"Mes {plan.get('mes')}: margen base {plan.get('margen_base')} EUR. "
            f"Mejor accion {plan.get('mejor_accion')} deja margen {plan.get('margen_mejor_accion')} EUR; "
            f"con cocina {plan.get('margen_combinado')} EUR. {plan.get('decision')}"
        )
        if crear_recomendacion(
            conn,
            empresa_id,
            "Direccion",
            "laboratorio_decision_mensual",
            prioridad,
            titulo,
            detalle_plan,
            plan.get("condicion_no_arruinarse") or "Ejecutar solo con control semanal y margen neto minimo.",
            {"plan": plan},
            "simulador_hotel1_laboratorio",
        ):
            recs += 1
    return {"memoria": res, "recomendaciones": recs, "eventos": eventos}


def main() -> None:
    args = parse_args()
    conn = connect(Path(args.db))
    ensure_schema(conn)
    resultado = {}
    if args.limpiar_ruido_hotel1_main:
        resultado["limpieza_hotel1_main"] = limpiar_ruido_hotel1_main(conn)
    resultado["centros"] = aprender_prueba_centros(
        conn,
        omitir_hotel1_main=bool(args.incluir_hotel1 and not args.incluir_hotel1_main),
    )
    if args.incluir_hotel1:
        resultado["hotel1"] = aprender_hotel1(conn, Path(args.hotel1_db))
    conn.commit()
    if args.json:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    else:
        print(resultado)


if __name__ == "__main__":
    main()

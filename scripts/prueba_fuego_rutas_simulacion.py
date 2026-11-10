# -*- coding: utf-8 -*-
"""Prueba de fuego de rutas principales con la base simulada.

Usa el test client de Flask con sesion de superadmin simulada. No hace POST ni
modifica datos operativos: solo comprueba que las pantallas principales cargan.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test de rutas ERP con simulacion Hotel 1.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not table_exists(conn, table):
        return False
    return any(r["name"] == column for r in conn.execute(f"PRAGMA table_info({table})").fetchall())


def latest_corrida(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "simulador_vida_corridas"):
        return 0
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def first_id(conn: sqlite3.Connection, table: str, where: str = "1=1", params=()) -> int | None:
    if not table_exists(conn, table):
        return None
    row = conn.execute(f"SELECT id FROM {table} WHERE {where} ORDER BY id DESC LIMIT 1", params).fetchone()
    return int(row["id"]) if row else None


def first_id_scoped(conn: sqlite3.Connection, table: str, centro_id: int | None = None) -> int | None:
    if not table_exists(conn, table):
        return None
    where = ["1=1"]
    params: list[object] = []
    if has_column(conn, table, "empresa_id"):
        where.append("empresa_id=?")
        params.append(EMPRESA_ID)
    if centro_id and has_column(conn, table, "centro_id"):
        where.append("centro_id=?")
        params.append(centro_id)
    elif centro_id:
        relaciones = {
            'habitacion_id': 'habitaciones_recepcion',
            'punto_venta_id': 'puntos_venta',
            'departamento_id': 'departamentos',
            'almacen_id': 'almacenes',
        }
        condiciones = []
        for clave, destino in relaciones.items():
            if has_column(conn, table, clave) and has_column(conn, destino, 'centro_id'):
                condiciones.append(
                    f'EXISTS (SELECT 1 FROM {destino} relacionado '
                    f'WHERE relacionado.id = {table}.{clave} AND relacionado.centro_id = ? '
                    'AND relacionado.empresa_id = ?)'
                )
                params.extend([centro_id, EMPRESA_ID])
        if not condiciones:
            return None
        where.append('(' + ' OR '.join(condiciones) + ')')
    return first_id(conn, table, " AND ".join(where), params)


def first_value(conn: sqlite3.Connection, table: str, column: str, where: str = "1=1", params=()) -> str | None:
    if not table_exists(conn, table):
        return None
    row = conn.execute(f"SELECT {column} AS value FROM {table} WHERE {where} ORDER BY id DESC LIMIT 1", params).fetchone()
    return str(row["value"]) if row and row["value"] is not None else None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_prueba_fuego_rutas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            modulo TEXT,
            ruta TEXT,
            status_code INTEGER,
            ok INTEGER DEFAULT 0,
            tiempo_ms REAL DEFAULT 0,
            bytes_respuesta INTEGER DEFAULT 0,
            error TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_prueba_fuego_resumen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            total INTEGER DEFAULT 0,
            ok INTEGER DEFAULT 0,
            fallos INTEGER DEFAULT 0,
            score REAL DEFAULT 0,
            lectura TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def build_routes(conn: sqlite3.Connection, centro_id: int) -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = [
        ("inicio", "/"),
        ("demo", "/demo/sala-control"),
        ("recepcion", "/recepcion/"),
        ("recepcion", "/recepcion/reservas"),
        ("recepcion", "/recepcion/ocupacion"),
        ("recepcion", "/recepcion/overbooking-control"),
        ("recepcion", "/recepcion/registro-viajeros"),
        ("recepcion", "/recepcion/prevision-servicios"),
        ("rrhh", "/rrhh/"),
        ("rrhh", "/rrhh/empleados"),
        ("rrhh", "/rrhh/plantilla-operativa"),
        ("rrhh", "/rrhh/cuadrantes/real?centro_id=3&fecha=2026-08-01"),
        ("rrhh", "/rrhh/fichajes?centro_id=3&fecha=2026-08-01"),
        ("rrhh", "/rrhh/prenomina"),
        ("rrhh", "/rrhh/necesidades-operativas"),
        ("produccion", "/produccion/?fecha=2026-08-01"),
        ("produccion", "/produccion/tarjetas?fecha=2026-08-01"),
        ("produccion", "/produccion/semanal"),
        ("produccion", "/produccion/trazabilidad"),
        ("compras", "/compras/pedidos"),
        ("compras", "/compras/recepcion"),
        ("compras", "/compras/necesidades"),
        ("compras", "/compras/incidencias"),
        ("compras", "/compras/auditoria"),
        ("almacenes", "/almacenes/"),
        ("inventario", "/inventario/"),
        ("inventario", "/inventario/revista"),
        ("appcc", "/appcc/"),
        ("appcc", "/appcc/operativa"),
        ("appcc", "/appcc/cierre-diario"),
        ("mantenimiento", "/mantenimiento/"),
        ("mantenimiento", "/mantenimiento/ordenes"),
        ("mantenimiento", "/mantenimiento/preventivo"),
        ("tpv", "/tpv/"),
        ("tpv", "/tpv/buffet"),
        ("tpv", "/tpv/cuentas"),
        ("buffet_ia", "/buffet-ia/"),
        ("asistente", "/asistente/gestion"),
        ("asistente", "/asistente/decision"),
        ("asistente", "/asistente/decision/chat"),
        ("contabilidad", "/contabilidad/"),
        ("contabilidad", "/contabilidad/explotacion"),
        ("animacion", "/animacion/"),
    ]
    reserva_id = first_id_scoped(conn, "reservas_recepcion", centro_id)
    if reserva_id:
        routes.append(("recepcion", f"/recepcion/reservas/{reserva_id}"))
    parte_id = first_id_scoped(conn, "partes_produccion", centro_id)
    if parte_id:
        routes.append(("produccion", f"/produccion/{parte_id}"))
        routes.append(("produccion", f"/produccion/{parte_id}/tarjetas"))
    pedido_id = first_id_scoped(conn, "pedidos", centro_id)
    if pedido_id:
        routes.append(("compras", f"/compras/pedidos/{pedido_id}"))
    recepcion_id = first_id_scoped(conn, "recepciones", centro_id)
    if recepcion_id:
        routes.append(("compras", f"/compras/recepciones/{recepcion_id}/conformidad"))
    almacen_id = first_id_scoped(conn, "almacenes", centro_id)
    if almacen_id:
        routes.append(("almacenes", f"/almacenes/{almacen_id}"))
    orden_id = first_id_scoped(conn, "ordenes_trabajo", centro_id)
    if orden_id:
        routes.append(("mantenimiento", f"/mantenimiento/ordenes/{orden_id}"))
    equipo_id = first_id_scoped(conn, "equipos_mantenimiento", centro_id)
    if equipo_id:
        routes.append(("mantenimiento", f"/mantenimiento/equipos/{equipo_id}"))
    terminal_id = first_id_scoped(conn, "tpv_terminales", centro_id)
    if terminal_id:
        routes.append(("tpv", f"/tpv/comandero/{terminal_id}"))
        routes.append(("tpv", f"/tpv/terminal/{terminal_id}"))
    montaje_id = first_id_scoped(conn, "montajes_buffet", centro_id)
    if montaje_id:
        routes.append(("buffet_ia", f"/buffet-ia/montajes/{montaje_id}"))
    codigo_tarjeta = first_value(conn, "produccion_tarjetas_servicio", "codigo", "empresa_id=?", (EMPRESA_ID,))
    if codigo_tarjeta:
        routes.append(("produccion", f"/produccion/tarjeta-digital/{codigo_tarjeta}"))
    return routes


def persist(conn: sqlite3.Connection, corrida_id: int, centro_id: int, results: list[dict]) -> None:
    conn.execute("DELETE FROM simulador_prueba_fuego_rutas WHERE corrida_id=? AND empresa_id=? AND centro_id=?", (corrida_id, EMPRESA_ID, centro_id))
    conn.execute("DELETE FROM simulador_prueba_fuego_resumen WHERE corrida_id=? AND empresa_id=? AND centro_id=?", (corrida_id, EMPRESA_ID, centro_id))
    for r in results:
        conn.execute(
            """
            INSERT INTO simulador_prueba_fuego_rutas
            (corrida_id, modulo, ruta, status_code, ok, tiempo_ms, bytes_respuesta, error, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                r["modulo"],
                r["ruta"],
                r.get("status_code"),
                1 if r["ok"] else 0,
                r["tiempo_ms"],
                r["bytes"],
                r["error"],
                EMPRESA_ID,
                centro_id,
            ),
        )
    total = len(results)
    ok = sum(1 for r in results if r["ok"])
    fallos = total - ok
    score = round((ok / total * 100), 1) if total else 0
    lectura = "Las pantallas principales cargan correctamente." if score >= 95 else "Hay rutas que fallan: revisar antes de seguir ampliando simulacion."
    conn.execute(
        """
        INSERT INTO simulador_prueba_fuego_resumen
        (corrida_id, total, ok, fallos, score, lectura, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (corrida_id, total, ok, fallos, score, lectura, EMPRESA_ID, centro_id),
    )
    conn.commit()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    os.environ["ERP_DATABASE"] = str(db_path)
    os.environ.setdefault("ERP_PORT", "5002")
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            corrida_id = 0
        routes = build_routes(conn, args.centro_id)
    finally:
        conn.close()

    from app import app  # noqa: WPS433

    app.config["TESTING"] = True
    results: list[dict] = []
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess.clear()
            sess["user_id"] = 5
            sess["user_nombre"] = "Super Admin"
            sess["user_email"] = "restelraco@gmail.com"
            sess["user_rol"] = "admin"
            sess["user_empresa_id"] = EMPRESA_ID
            sess["user_es_global"] = 1
            sess["centro_activo_id"] = args.centro_id
            sess["puede_ver_precios"] = 1
            sess["puede_modificar_precios"] = 1
            sess["puede_validar_recepcion"] = 1
            sess["puede_validar_economicamente"] = 1
        for modulo, ruta in routes:
            start = time.perf_counter()
            status_code = 0
            error = ""
            size = 0
            ok = False
            try:
                resp = client.get(ruta, follow_redirects=False)
                status_code = int(resp.status_code)
                size = len(resp.get_data() or b"")
                ok = status_code < 400
                if status_code >= 400:
                    error = (resp.get_data(as_text=True) or "")[:900]
            except Exception:
                error = traceback.format_exc(limit=8)
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            results.append({
                "modulo": modulo,
                "ruta": ruta,
                "status_code": status_code,
                "ok": ok,
                "tiempo_ms": elapsed,
                "bytes": size,
                "error": error,
            })

    conn = connect(db_path)
    try:
        ensure_schema(conn)
        persist(conn, corrida_id, args.centro_id, results)
    finally:
        conn.close()
    ok_count = sum(1 for r in results if r["ok"])
    failures = [r for r in results if not r["ok"]]
    print({"corrida_id": corrida_id, "rutas": len(results), "ok": ok_count, "fallos": len(failures), "score": round(ok_count / max(len(results), 1) * 100, 1)})
    for f in failures[:12]:
        print(f"{f['status_code']} {f['ruta']} :: {f['error'][:180].replace(chr(10), ' ')}")


if __name__ == "__main__":
    main()

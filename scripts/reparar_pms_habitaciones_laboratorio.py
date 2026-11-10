# -*- coding: utf-8 -*-
"""Repara conflictos PMS de habitaciones en el laboratorio.

No borra reservas. Intenta:
- asignar habitacion a reservas sin habitacion;
- mover reservas solapadas, priorizando las reservas OB-* de overbooking;
- dejar trazabilidad en una tabla de reparaciones y observaciones operativas.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reparar asignaciones PMS del laboratorio.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=3)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--dry-run", action="store_true")
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
        CREATE TABLE IF NOT EXISTS simulador_pms_reparaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP,
            empresa_id INTEGER,
            centro_id INTEGER,
            reserva_id INTEGER,
            codigo TEXT,
            accion TEXT,
            habitacion_origen_id INTEGER,
            habitacion_destino_id INTEGER,
            resultado TEXT,
            detalle TEXT
        )
        """
    )


def room_is_free(conn: sqlite3.Connection, room_id: int, entrada: str, salida: str, empresa_id: int, exclude_reserva_id: int | None = None) -> bool:
    params: list[object] = [room_id, empresa_id, salida, entrada]
    exclude = ""
    if exclude_reserva_id:
        exclude = "AND id<>?"
        params.append(exclude_reserva_id)
    row = conn.execute(
        f"""
        SELECT COUNT(*) total
        FROM reservas_recepcion
        WHERE habitacion_id=?
          AND empresa_id=?
          AND COALESCE(estado,'') NOT IN ('cancelada','cancelado','checkout','no_show','no show')
          AND date(fecha_entrada) < date(?)
          AND date(fecha_salida) > date(?)
          {exclude}
        """,
        params,
    ).fetchone()
    if int(row["total"] or 0) > 0:
        return False
    if table_exists(conn, "bloqueos_habitacion_recepcion"):
        block = conn.execute(
            """
            SELECT COUNT(*) total
            FROM bloqueos_habitacion_recepcion
            WHERE habitacion_id=? AND empresa_id=?
              AND COALESCE(estado,'activo')='activo'
              AND date(fecha_inicio) < date(?)
              AND date(fecha_fin) > date(?)
            """,
            (room_id, empresa_id, salida, entrada),
        ).fetchone()
        if int(block["total"] or 0) > 0:
            return False
    return True


def find_room(conn: sqlite3.Connection, reserva: sqlite3.Row, empresa_id: int, centro_id: int, allow_upgrade: bool = True) -> sqlite3.Row | None:
    pax = int(reserva["adultos"] or 0) + int(reserva["menores"] or 0)
    pax = max(pax, 1)
    tipo_id = reserva["tipo_habitacion_id"]
    params: list[object] = [empresa_id, centro_id, pax]
    tipo_clause = ""
    order_params: list[object] = [tipo_id or 0]
    if tipo_id and allow_upgrade:
        tipo_clause = "AND (tipo_habitacion_id=? OR capacidad>=?)"
        params.extend([tipo_id, pax])
    elif tipo_id:
        tipo_clause = "AND tipo_habitacion_id=?"
        params.append(tipo_id)
    rooms = conn.execute(
        f"""
        SELECT id, numero, tipo, tipo_habitacion_id, capacidad
        FROM habitaciones_recepcion
        WHERE empresa_id=? AND centro_id=? AND COALESCE(activo,1)=1 AND capacidad>=?
          {tipo_clause}
        ORDER BY CASE WHEN tipo_habitacion_id=? THEN 0 ELSE 1 END, capacidad, numero
        """,
        params + order_params,
    ).fetchall()
    for room in rooms:
        if room_is_free(conn, int(room["id"]), reserva["fecha_entrada"], reserva["fecha_salida"], empresa_id, int(reserva["id"])):
            return room
    return None


def log_repair(conn: sqlite3.Connection, empresa_id: int, centro_id: int, reserva: sqlite3.Row, accion: str, origen: int | None, destino: int | None, resultado: str, detalle: str) -> None:
    conn.execute(
        """
        INSERT INTO simulador_pms_reparaciones
        (empresa_id, centro_id, reserva_id, codigo, accion, habitacion_origen_id,
         habitacion_destino_id, resultado, detalle)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (empresa_id, centro_id, reserva["id"], reserva["codigo"], accion, origen, destino, resultado, detalle),
    )


def append_observacion(conn: sqlite3.Connection, reserva_id: int, texto: str) -> None:
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET observaciones_operativas =
            TRIM(COALESCE(observaciones_operativas,'') || char(10) || ?),
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (texto, reserva_id),
    )


def assign_room(conn: sqlite3.Connection, reserva: sqlite3.Row, room: sqlite3.Row, accion: str, dry_run: bool) -> str:
    detalle = f"{accion}: reserva {reserva['codigo']} -> habitacion {room['numero']} ({room['id']})."
    if not dry_run:
        conn.execute(
            "UPDATE reservas_recepcion SET habitacion_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (room["id"], reserva["id"]),
        )
        append_observacion(conn, int(reserva["id"]), f"[PMS laboratorio {datetime.now().isoformat(timespec='seconds')}] {detalle}")
    return detalle


def reservas_sin_habitacion(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM reservas_recepcion
        WHERE empresa_id=? AND centro_id=?
          AND fecha_entrada LIKE ?
          AND COALESCE(estado,'') NOT IN ('cancelada','cancelado','checkout','no_show','no show')
          AND (habitacion_id IS NULL OR habitacion_id=0)
        ORDER BY fecha_entrada, id
        """,
        (empresa_id, centro_id, f"{year}-%"),
    ).fetchall()


def solapes(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT a.id a_id, a.codigo a_codigo, b.id b_id, b.codigo b_codigo,
               a.habitacion_id, a.fecha_entrada, a.fecha_salida,
               b.fecha_entrada b_entrada, b.fecha_salida b_salida
        FROM reservas_recepcion a
        JOIN reservas_recepcion b
          ON a.id < b.id
         AND a.empresa_id=b.empresa_id
         AND a.centro_id=b.centro_id
         AND a.habitacion_id=b.habitacion_id
         AND a.habitacion_id IS NOT NULL
         AND date(a.fecha_entrada) < date(b.fecha_salida)
         AND date(b.fecha_entrada) < date(a.fecha_salida)
        WHERE a.empresa_id=? AND a.centro_id=?
          AND a.fecha_entrada LIKE ?
          AND COALESCE(a.estado,'') NOT IN ('cancelada','cancelado','checkout','no_show','no show')
          AND COALESCE(b.estado,'') NOT IN ('cancelada','cancelado','checkout','no_show','no show')
        ORDER BY a.fecha_entrada, a.id
        """,
        (empresa_id, centro_id, f"{year}-%"),
    ).fetchall()


def reserva_by_id(conn: sqlite3.Connection, reserva_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM reservas_recepcion WHERE id=?", (reserva_id,)).fetchone()


def choose_conflict_reservation(conn: sqlite3.Connection, pair: sqlite3.Row) -> sqlite3.Row:
    a = reserva_by_id(conn, int(pair["a_id"]))
    b = reserva_by_id(conn, int(pair["b_id"]))
    a_ob = (a["codigo"] or "").startswith("OB-")
    b_ob = (b["codigo"] or "").startswith("OB-")
    if b_ob and not a_ob:
        return b
    if a_ob and not b_ob:
        return a
    return b


def repair(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int, dry_run: bool) -> dict:
    result = {"sin_habitacion_resueltas": 0, "solapes_resueltos": 0, "escaladas": 0}

    for reserva in reservas_sin_habitacion(conn, empresa_id, centro_id, year):
        room = find_room(conn, reserva, empresa_id, centro_id, allow_upgrade=True)
        if room:
            detalle = assign_room(conn, reserva, room, "asignacion_sin_habitacion", dry_run)
            if not dry_run:
                log_repair(conn, empresa_id, centro_id, reserva, "asignacion_sin_habitacion", None, int(room["id"]), "resuelto", detalle)
            result["sin_habitacion_resueltas"] += 1
        else:
            if not dry_run:
                log_repair(conn, empresa_id, centro_id, reserva, "asignacion_sin_habitacion", None, None, "escalado", "No hay habitacion libre compatible.")
            result["escaladas"] += 1

    # Iteramos hasta que no queden solapes resolubles o se alcance un limite prudente.
    for _ in range(5):
        pairs = solapes(conn, empresa_id, centro_id, year)
        if not pairs:
            break
        moved = 0
        for pair in pairs:
            reserva = choose_conflict_reservation(conn, pair)
            room = find_room(conn, reserva, empresa_id, centro_id, allow_upgrade=True)
            if room:
                origen = int(reserva["habitacion_id"] or pair["habitacion_id"])
                detalle = assign_room(conn, reserva, room, "reasignacion_solape", dry_run)
                if not dry_run:
                    log_repair(conn, empresa_id, centro_id, reserva, "reasignacion_solape", origen, int(room["id"]), "resuelto", detalle)
                result["solapes_resueltos"] += 1
                moved += 1
            else:
                if not dry_run:
                    log_repair(conn, empresa_id, centro_id, reserva, "reasignacion_solape", int(reserva["habitacion_id"] or 0), None, "escalado", "No hay habitacion libre para resolver solape.")
                result["escaladas"] += 1
        if dry_run or moved == 0:
            break
    result["sin_habitacion_restantes"] = len(reservas_sin_habitacion(conn, empresa_id, centro_id, year))
    result["solapes_restantes"] = len(solapes(conn, empresa_id, centro_id, year))
    return result


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        if not args.dry_run:
            conn.execute(
                "DELETE FROM simulador_pms_reparaciones WHERE empresa_id=? AND centro_id=?",
                (args.empresa_id, args.centro_id),
            )
        result = repair(conn, args.empresa_id, args.centro_id, args.year, args.dry_run)
        if not args.dry_run:
            conn.commit()
        print(result)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

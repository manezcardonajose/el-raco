# -*- coding: utf-8 -*-
"""Desvia overbooking irresoluble del laboratorio a un hotel de apoyo.

Cuando Hotel 1 no tiene hueco interno, una reserva OB-* debe salir del centro
origen y quedar trazada como desvio a hotel de apoyo, no permanecer pisando una
habitacion ocupada.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Desviar overbooking de laboratorio a hotel de apoyo.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=3)
    parser.add_argument("--year", type=int, default=2026)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


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


def get_support_center(conn: sqlite3.Connection, empresa_id: int) -> int:
    row = conn.execute(
        "SELECT id FROM centros_trabajo WHERE empresa_id=? AND codigo='APOYO_PMS_LAB'",
        (empresa_id,),
    ).fetchone()
    if row:
        conn.execute("UPDATE centros_trabajo SET activo=1 WHERE id=?", (row["id"],))
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO centros_trabajo
        (nombre, codigo, direccion, descripcion, empresa_id, activo, motivo_cambio)
        VALUES ('Hotel apoyo PMS Lab', 'APOYO_PMS_LAB', 'Simulacion',
                'Centro de apoyo creado para desviar overbooking de laboratorio.',
                ?, 1, 'Simulacion PMS')
        """,
        (empresa_id,),
    )
    return int(cur.lastrowid)


def support_room(conn: sqlite3.Connection, empresa_id: int, support_center_id: int, reserva: sqlite3.Row, seq: int) -> int:
    numero = f"AP-{reserva['id']}"
    row = conn.execute(
        """
        SELECT id FROM habitaciones_recepcion
        WHERE empresa_id=? AND centro_id=? AND numero=?
        """,
        (empresa_id, support_center_id, numero),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO habitaciones_recepcion
        (numero, nombre, tipo, planta, capacidad, estado, centro_id, observaciones,
         empresa_id, activo, tipo_habitacion_id, bloque)
        VALUES (?, ?, ?, 'apoyo', ?, 'reservada', ?, ?, ?, 1, ?, 'Hotel apoyo')
        """,
        (
            numero,
            f"Apoyo overbooking {seq}",
            reserva["tipo_habitacion_id"] or "apoyo",
            max(1, int(reserva["adultos"] or 0) + int(reserva["menores"] or 0)),
            support_center_id,
            f"Habitacion de apoyo para reserva {reserva['codigo']}.",
            empresa_id,
            reserva["tipo_habitacion_id"],
        ),
    )
    return int(cur.lastrowid)


def remaining_unassigned(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM reservas_recepcion
        WHERE empresa_id=? AND centro_id=?
          AND fecha_entrada LIKE ?
          AND codigo LIKE 'OB-%'
          AND COALESCE(estado,'') NOT IN ('cancelada','cancelado','checkout','no_show','no show')
          AND (habitacion_id IS NULL OR habitacion_id=0)
        ORDER BY fecha_entrada, id
        """,
        (empresa_id, centro_id, f"{year}-%"),
    ).fetchall()


def remaining_overlap_ob(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT b.*
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
          AND b.codigo LIKE 'OB-%'
          AND COALESCE(a.estado,'') NOT IN ('cancelada','cancelado','checkout','no_show','no show')
          AND COALESCE(b.estado,'') NOT IN ('cancelada','cancelado','checkout','no_show','no show')
        ORDER BY a.fecha_entrada, b.id
        """,
        (empresa_id, centro_id, f"{year}-%"),
    ).fetchall()
    seen: set[int] = set()
    unique = []
    for row in rows:
        if int(row["id"]) in seen:
            continue
        seen.add(int(row["id"]))
        unique.append(row)
    return unique


def append_obs(conn: sqlite3.Connection, reserva_id: int, texto: str) -> None:
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET observaciones_operativas = TRIM(COALESCE(observaciones_operativas,'') || char(10) || ?),
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (texto, reserva_id),
    )


def desviar(conn: sqlite3.Connection, empresa_id: int, centro_id: int, support_center_id: int, reserva: sqlite3.Row, seq: int, motivo: str) -> None:
    room_id = support_room(conn, empresa_id, support_center_id, reserva, seq)
    origen = reserva["habitacion_id"]
    detalle = (
        f"Reserva {reserva['codigo']} desviada a Hotel apoyo PMS Lab "
        f"por {motivo}. Centro origen {centro_id}, habitacion origen {origen or 'sin asignar'}."
    )
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET centro_id=?, habitacion_id=?, estado_externo='desvio_hotel_apoyo',
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (support_center_id, room_id, reserva["id"]),
    )
    append_obs(conn, int(reserva["id"]), f"[PMS laboratorio {datetime.now().isoformat(timespec='seconds')}] {detalle}")
    conn.execute(
        """
        INSERT INTO simulador_pms_reparaciones
        (empresa_id, centro_id, reserva_id, codigo, accion, habitacion_origen_id,
         habitacion_destino_id, resultado, detalle)
        VALUES (?, ?, ?, ?, 'desvio_hotel_apoyo', ?, ?, 'resuelto', ?)
        """,
        (empresa_id, centro_id, reserva["id"], reserva["codigo"], origen, room_id, detalle),
    )


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        support_center_id = get_support_center(conn, args.empresa_id)
        reservas = remaining_unassigned(conn, args.empresa_id, args.centro_id, args.year)
        reservas += remaining_overlap_ob(conn, args.empresa_id, args.centro_id, args.year)
        seen: set[int] = set()
        count = 0
        for reserva in reservas:
            if int(reserva["id"]) in seen:
                continue
            seen.add(int(reserva["id"]))
            motivo = "sin habitacion" if not reserva["habitacion_id"] else "solape sin hueco interno"
            desviar(conn, args.empresa_id, args.centro_id, support_center_id, reserva, count + 1, motivo)
            count += 1
        conn.commit()
        print({"desviadas": count, "centro_apoyo_id": support_center_id})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

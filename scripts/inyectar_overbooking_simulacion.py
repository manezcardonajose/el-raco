# -*- coding: utf-8 -*-
"""Inyecta casos de overbooking en la simulacion sin borrar datos."""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crear casos de overbooking sobre simulacion existente.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--casos", type=int, default=18)
    parser.add_argument("--seed", type=int, default=20260527)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_overbooking_casos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha_deteccion TEXT,
            fecha_entrada TEXT,
            fecha_salida TEXT,
            tipo TEXT,
            severidad TEXT,
            reserva_id INTEGER,
            reserva_conflicto_id INTEGER,
            habitacion_id INTEGER,
            habitaciones_afectadas INTEGER,
            pax_afectados INTEGER,
            estado_gestion TEXT,
            decision_recomendada TEXT,
            coste_estimado REAL,
            resultado_simulado TEXT,
            evidencia_json TEXT,
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def latest_corrida(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def insert_event(conn: sqlite3.Connection, corrida_id: int, fecha: str, tipo: str, severidad: str, descripcion: str, table: str, row_id: int, impacto: dict) -> None:
    conn.execute(
        """
        INSERT INTO simulador_vida_eventos
        (corrida_id, fecha_simulada, modulo, tipo, severidad, descripcion, tabla_ref,
         registro_id, impacto_json, empresa_id, centro_id)
        VALUES (?, ?, 'recepcion', ?, ?, ?, ?, ?, ?, 1, 3)
        """,
        (corrida_id, fecha, tipo, severidad, descripcion, table, row_id, json.dumps(impacto, ensure_ascii=False)),
    )


def pick_base_reservas(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.*, h.numero AS habitacion_numero, h.tipo_habitacion_id AS habitacion_tipo_id
        FROM reservas_recepcion r
        JOIN habitaciones_recepcion h ON h.id=r.habitacion_id
        WHERE r.empresa_id=1
          AND r.codigo LIKE 'VIDA-H1-%'
          AND r.habitacion_id IS NOT NULL
          AND r.fecha_entrada >= '2026-06-05'
          AND r.fecha_salida <= '2027-02-25'
          AND r.estado IN ('confirmada','alojado','checkin')
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def insert_overbooking_reserva(conn: sqlite3.Connection, base: sqlite3.Row, serial: int, mode: str) -> int:
    entrada = date.fromisoformat(base["fecha_entrada"])
    salida = date.fromisoformat(base["fecha_salida"])
    if (salida - entrada).days > 5:
        salida = entrada + timedelta(days=random.randint(2, 5))
    habitacion_id = None if mode == "sin_asignar" else base["habitacion_id"]
    precio = round(float(base["precio_total"] or 0) * random.uniform(0.88, 1.28), 2)
    codigo = f"OB-H1-{entrada.strftime('%Y%m%d')}-{serial:04d}"
    cur = conn.execute(
        """
        INSERT INTO reservas_recepcion
        (codigo, habitacion_id, tipo_habitacion_id, centro_id, fecha_entrada, fecha_salida,
         hora_llegada, hora_salida, regimen, adultos, menores, estado, origen,
         titular_nombre, titular_email, titular_telefono, precio_total, deposito, saldo,
         observaciones, observaciones_operativas, empresa_id, localizador_externo,
         politica_credito, limite_credito)
        VALUES (?, ?, ?, 3, ?, ?, '18:30', '11:00', ?, ?, ?, 'confirmada', ?,
                ?, ?, '+34 600 900 900', ?, ?, ?, ?, ?, 1, ?, 'abierto', 100)
        """,
        (
            codigo,
            habitacion_id,
            base["tipo_habitacion_id"] or base["habitacion_tipo_id"],
            entrada.isoformat(),
            salida.isoformat(),
            base["regimen"] or "media_pension",
            int(base["adultos"] or 2),
            int(base["menores"] or 0),
            random.choice(["booking", "tour_operador", "web", "grupo"]),
            f"Overbooking Cliente {serial}",
            f"overbooking{serial}@hotel1.demo",
            precio,
            round(precio * 0.20, 2),
            round(precio * 0.80, 2),
            "Reserva creada para forzar overbooking en simulacion",
            f"overbooking={mode}; conflicto_base={base['codigo']}",
            f"OB-{serial:06d}",
        ),
    )
    return int(cur.lastrowid)


def insert_case(conn: sqlite3.Connection, corrida_id: int, base: sqlite3.Row, reserva_id: int, mode: str, serial: int) -> int:
    pax = int(base["adultos"] or 0) + int(base["menores"] or 0)
    if mode == "doble_venta":
        decision = "Reasignar una reserva a habitacion libre del mismo tipo; si no existe, upgrade sin coste o hotel alternativo."
        coste = random.choice([0, 45, 90, 160])
        severidad = "critica"
    elif mode == "sin_asignar":
        decision = "Asignar habitacion antes de llegada; si no hay cupo, llamar cliente y ofrecer upgrade/traslado."
        coste = random.choice([0, 35, 75])
        severidad = "alta"
    else:
        decision = "Bloqueo por averia encima de reserva: priorizar reparacion o mover reserva antes del check-in."
        coste = random.choice([80, 140, 250])
        severidad = "critica"
    cur = conn.execute(
        """
        INSERT INTO simulador_overbooking_casos
        (corrida_id, fecha_deteccion, fecha_entrada, fecha_salida, tipo, severidad,
         reserva_id, reserva_conflicto_id, habitacion_id, habitaciones_afectadas,
         pax_afectados, estado_gestion, decision_recomendada, coste_estimado,
         resultado_simulado, evidencia_json, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 1, 3)
        """,
        (
            corrida_id,
            base["fecha_entrada"],
            base["fecha_entrada"],
            base["fecha_salida"],
            mode,
            severidad,
            reserva_id,
            base["id"],
            base["habitacion_id"],
            pax,
            random.choice(["pendiente", "en_gestion", "resuelto_simulado"]),
            decision,
            coste,
            random.choice(["upgrade", "reubicacion interna", "hotel alternativo", "pendiente direccion"]),
            json.dumps({"reserva_base": base["codigo"], "habitacion": base["habitacion_numero"]}, ensure_ascii=False),
        ),
    )
    case_id = int(cur.lastrowid)
    insert_event(
        conn,
        corrida_id,
        base["fecha_entrada"],
        "overbooking",
        severidad,
        f"Overbooking {mode} habitacion {base['habitacion_numero']} reserva base {base['codigo']}",
        "simulador_overbooking_casos",
        case_id,
        {"reserva_id": reserva_id, "conflicto_id": base["id"], "coste_estimado": coste},
    )
    return case_id


def insert_block_case(conn: sqlite3.Connection, corrida_id: int, base: sqlite3.Row, serial: int) -> int:
    conn.execute(
        """
        INSERT INTO bloqueos_habitacion_recepcion
        (habitacion_id, fecha_inicio, fecha_fin, motivo, descripcion, estado, empresa_id, creado_por)
        VALUES (?, ?, ?, 'fuera_servicio', ?, 'activo', 1, 'Simulador overbooking')
        """,
        (
            base["habitacion_id"],
            base["fecha_entrada"],
            base["fecha_salida"],
            f"Averia critica simulada que pisa reserva {base['codigo']}",
        ),
    )
    return insert_case(conn, corrida_id, base, int(base["id"]), "bloqueo_sobre_reserva", serial)


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
        bases = pick_base_reservas(conn, args.casos)
        created = 0
        for serial, base in enumerate(bases, start=1):
            mode = random.choice(["doble_venta", "doble_venta", "sin_asignar", "bloqueo_sobre_reserva"])
            if mode == "bloqueo_sobre_reserva":
                insert_block_case(conn, corrida_id, base, serial)
            else:
                reserva_id = insert_overbooking_reserva(conn, base, serial, mode)
                insert_case(conn, corrida_id, base, reserva_id, mode, serial)
            created += 1
        conn.commit()
        print(f"Overbooking inyectado sin borrar: {created} casos en {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Genera ocupacion sintetica 2027 para Hotel Albarracin sin tocar produccion."""

from __future__ import annotations

import argparse
import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
EMPRESA_ID = 2
CENTRO_ID = 8
ORIGEN = "simulador_albarracin_ocupacion"


@dataclass
class Room:
    id: int
    numero: str
    tipo: str
    capacidad: int
    tipo_habitacion_id: int | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sembrar ocupacion Albarracin 2027.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--year", type=int, default=2027)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def daterange(start: date, end: date):
    cur = start
    while cur < end:
        yield cur
        cur += timedelta(days=1)


def sunday_on_or_after(day: date) -> date:
    return day + timedelta(days=(6 - day.weekday()) % 7)


def channel(conn: sqlite3.Connection, empresa_id: int, codigo: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM recepcion_canales_reserva WHERE empresa_id=? AND codigo=?",
        (empresa_id, codigo),
    ).fetchone()
    return int(row["id"]) if row else None


def rooms(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> list[Room]:
    rows = conn.execute(
        """
        SELECT id, numero, tipo, capacidad, tipo_habitacion_id
        FROM habitaciones_recepcion
        WHERE empresa_id=? AND centro_id=? AND COALESCE(activo,1)=1
        ORDER BY capacidad, numero
        """,
        (empresa_id, centro_id),
    ).fetchall()
    return [
        Room(int(r["id"]), r["numero"], r["tipo"] or "", int(r["capacidad"] or 2), r["tipo_habitacion_id"])
        for r in rows
    ]


def available_rooms(all_rooms: list[Room], occupied: dict[int, list[tuple[date, date]]], start: date, end: date) -> list[Room]:
    result = []
    for room in all_rooms:
        overlaps = False
        for a, b in occupied.get(room.id, []):
            if start < b and end > a:
                overlaps = True
                break
        if not overlaps:
            result.append(room)
    return result


def mark(occupied: dict[int, list[tuple[date, date]]], room_id: int, start: date, end: date) -> None:
    occupied.setdefault(room_id, []).append((start, end))


def choose_rooms(avail: list[Room], pax: int, target_rooms: int | None = None) -> list[Room]:
    doubles = [r for r in avail if r.capacidad == 2]
    triples = [r for r in avail if r.capacidad == 3]
    singles = [r for r in avail if r.capacidad == 1]
    quads = [r for r in avail if r.capacidad >= 4]
    ordered = doubles + triples + singles + quads
    if target_rooms is None:
        target_rooms = max(1, math.ceil(pax / 2))
    return ordered[: min(target_rooms, len(ordered))]


def price_per_room_night(segment: str, day: date, weekend: bool = False) -> float:
    if segment == "inserso":
        return 58.0
    if segment == "semana_santa":
        return 128.0 if weekend else 118.0
    if segment == "alta":
        return 112.0 if weekend else 96.0
    if segment == "media":
        return 88.0 if weekend else 74.0
    if segment == "baja":
        return 72.0 if weekend else 58.0
    return 68.0


def insert_reservation(
    conn: sqlite3.Connection,
    empresa_id: int,
    centro_id: int,
    room: Room,
    start: date,
    end: date,
    adultos: int,
    regimen: str,
    titular: str,
    canal_id: int | None,
    segment: str,
    seq: int,
    notes: str,
) -> int:
    nights = max((end - start).days, 1)
    weekend = any(d.weekday() >= 4 for d in daterange(start, end))
    pnr = price_per_room_night(segment, start, weekend)
    total = round(pnr * nights, 2)
    deposito = round(total * (0.15 if segment != "inserso" else 0.0), 2)
    saldo = round(total - deposito, 2)
    codigo = f"ALB-{segment.upper()[:3]}-{start.strftime('%Y%m%d')}-{room.numero}-{seq:03d}"
    comision = 0.0
    if canal_id:
        row = conn.execute("SELECT comision_pct FROM recepcion_canales_reserva WHERE id=?", (canal_id,)).fetchone()
        comision = float(row["comision_pct"] or 0) if row else 0.0
    cur = conn.execute(
        """
        INSERT INTO reservas_recepcion
        (codigo, habitacion_id, tipo_habitacion_id, fecha_entrada, fecha_salida,
         hora_llegada, hora_salida, regimen, adultos, menores, estado, origen,
         titular_nombre, titular_email, titular_telefono, precio_total, deposito, saldo,
         observaciones, observaciones_operativas, empresa_id, canal_id, localizador_externo,
         comision_canal, estado_externo, ultima_sincronizacion, politica_credito,
         preferencia_cama, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'confirmada', ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, 'confirmada', CURRENT_TIMESTAMP, 'abierto', ?, ?)
        """,
        (
            codigo,
            room.id,
            room.tipo_habitacion_id,
            start.isoformat(),
            end.isoformat(),
            "12:30" if segment == "inserso" else "15:00",
            "10:00",
            regimen,
            adultos,
            ORIGEN,
            titular,
            f"{codigo.lower()}@demo.local",
            "600000000",
            total,
            deposito,
            saldo,
            notes,
            f"Segmento {segment}. Generado para curva de ocupacion Albarracin.",
            empresa_id,
            canal_id,
            codigo,
            comision,
            "twin" if room.capacidad == 2 else "",
            centro_id,
        ),
    )
    return int(cur.lastrowid)


def add_restaurant_services(conn: sqlite3.Connection, reserva_id: int, empresa_id: int, centro_id: int, start: date, end: date, pax: int, segment: str, pv_id: int | None) -> int:
    if not pv_id:
        return 0
    count = 0
    for d in daterange(start, end):
        services = ["desayuno"]
        if segment == "inserso":
            if d == start:
                services.extend(["almuerzo", "cena"])
            elif d < end - timedelta(days=1):
                services.extend(["almuerzo", "cena"])
            else:
                services.extend(["cena"])
        elif segment in {"semana_santa", "alta", "media"}:
            services.append("cena")
        for service in services:
            hour = {"desayuno": "08:30", "almuerzo": "13:30", "cena": "20:30"}[service]
            conn.execute(
                """
                INSERT INTO recepcion_reservas_restaurante
                (reserva_id, punto_venta_id, fecha, hora, personas, estado, observaciones,
                 empresa_id, turno, nombre_contacto, telefono_contacto, origen)
                VALUES (?, ?, ?, ?, ?, 'confirmada', ?, ?, ?, ?, '600000000', ?)
                """,
                (
                    reserva_id,
                    pv_id,
                    d.isoformat(),
                    hour,
                    pax,
                    f"Servicio {service} generado por simulador ocupacion Albarracin.",
                    empresa_id,
                    service,
                    f"Reserva {reserva_id}",
                    ORIGEN,
                ),
            )
            count += 1
    return count


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_albarracin_ocupacion_resumen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER,
            centro_id INTEGER,
            year INTEGER,
            fecha TEXT,
            habitaciones_ocupadas INTEGER,
            habitaciones_total INTEGER,
            ocupacion_pct REAL,
            pax INTEGER,
            segmento TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, centro_id, year, fecha)
        )
        """
    )


def pv_restaurante(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM puntos_venta WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER('Restaurante') AND COALESCE(activo,1)=1",
        (empresa_id, centro_id),
    ).fetchone()
    return int(row["id"]) if row else None


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    rng = random.Random(20270115)
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        all_rooms = rooms(conn, args.empresa_id, args.centro_id)
        if not all_rooms:
            raise RuntimeError("No hay habitaciones activas en Hotel Albarracin.")

        conn.execute(
            "DELETE FROM recepcion_reservas_restaurante WHERE origen=? AND empresa_id=?",
            (ORIGEN, args.empresa_id),
        )
        conn.execute(
            "DELETE FROM reservas_recepcion WHERE origen=? AND empresa_id=? AND centro_id=?",
            (ORIGEN, args.empresa_id, args.centro_id),
        )
        conn.execute(
            "DELETE FROM simulador_albarracin_ocupacion_resumen WHERE empresa_id=? AND centro_id=? AND year=?",
            (args.empresa_id, args.centro_id, args.year),
        )

        canales = {
            "agencia": channel(conn, args.empresa_id, "agencia"),
            "web": channel(conn, args.empresa_id, "web"),
            "booking": channel(conn, args.empresa_id, "booking"),
            "telefono": channel(conn, args.empresa_id, "telefono"),
        }
        pv_id = pv_restaurante(conn, args.empresa_id, args.centro_id)
        occupied: dict[int, list[tuple[date, date]]] = {}
        created = restaurant_services = 0

        def reserve_block(start: date, end: date, pax: int, target_rooms: int, segment: str, regimen: str, canal_key: str, titular_prefix: str, notes: str) -> None:
            nonlocal created, restaurant_services
            avail = available_rooms(all_rooms, occupied, start, end)
            selected = choose_rooms(avail, pax, target_rooms)
            if not selected:
                return
            remaining = pax
            for idx, room in enumerate(selected, start=1):
                adultos = min(room.capacidad, max(1, math.ceil(remaining / max(len(selected) - idx + 1, 1))))
                remaining -= adultos
                rid = insert_reservation(
                    conn,
                    args.empresa_id,
                    args.centro_id,
                    room,
                    start,
                    end,
                    adultos,
                    regimen,
                    f"{titular_prefix} {start.strftime('%d/%m')}",
                    canales.get(canal_key),
                    segment,
                    idx,
                    notes,
                )
                restaurant_services += add_restaurant_services(conn, rid, args.empresa_id, args.centro_id, start, end, adultos, segment, pv_id)
                mark(occupied, room.id, start, end)
                created += 1

        # Imserso hasta la semana anterior a Semana Santa 2027.
        ss_start = date(args.year, 3, 19)
        ss_end = date(args.year, 4, 3)  # salida 3/4, 15 noches desde 19/3.
        cur = sunday_on_or_after(date(args.year, 1, 15))
        while cur + timedelta(days=5) <= ss_start:
            pax = rng.randint(60, 64)
            reserve_block(cur, cur + timedelta(days=5), pax, math.ceil(pax / 2), "inserso", "pension_completa", "agencia", "Grupo Imserso", "Entrada domingo con almuerzo y salida viernes con desayuno.")
            cur += timedelta(days=7)

        # Semana Santa 15 dias clientes normales sobre 80%.
        for block_start in [ss_start, ss_start + timedelta(days=5), ss_start + timedelta(days=10)]:
            block_end = min(block_start + timedelta(days=5), ss_end)
            rooms_target = round(len(all_rooms) * 0.80)
            pax = rooms_target * 2 + rng.randint(-4, 5)
            reserve_block(block_start, block_end, pax, rooms_target, "semana_santa", "media_pension", "web", "Semana Santa", "Cliente normal Semana Santa 80% aproximado.")

        # Imserso posterior hasta finales de mayo.
        cur = sunday_on_or_after(ss_end + timedelta(days=1))
        while cur <= date(args.year, 5, 31):
            pax = rng.randint(60, 64)
            reserve_block(cur, cur + timedelta(days=5), pax, math.ceil(pax / 2), "inserso", "pension_completa", "agencia", "Grupo Imserso", "Tramo Imserso posterior a Semana Santa.")
            cur += timedelta(days=7)

        # Imserso noviembre hasta 20 diciembre.
        cur = sunday_on_or_after(date(args.year, 11, 1))
        while cur + timedelta(days=5) <= date(args.year, 12, 20):
            pax = rng.randint(60, 64)
            reserve_block(cur, cur + timedelta(days=5), pax, math.ceil(pax / 2), "inserso", "pension_completa", "agencia", "Grupo Imserso", "Tramo Imserso noviembre-diciembre.")
            cur += timedelta(days=7)

        # Temporada normal y alta: ocupacion tambien entre semana, no solo fin de semana.
        cur = date(args.year, 6, 1)
        while cur <= date(args.year, 9, 15):
            if cur.weekday() == 0:
                if cur < date(args.year, 7, 1):
                    pct = rng.uniform(0.45, 0.60)
                    segment = "media"
                elif cur < date(args.year, 8, 1):
                    pct = rng.uniform(0.62, 0.76)
                    segment = "alta"
                elif cur < date(args.year, 9, 1):
                    pct = rng.uniform(0.78, 0.92)
                    segment = "alta"
                else:
                    pct = rng.uniform(0.60, 0.76)
                    segment = "media"
                target = round(len(all_rooms) * pct)
                pax = target * 2 + rng.randint(-5, 6)
                reserve_block(cur, min(cur + timedelta(days=4), date(args.year, 9, 16)), pax, target, segment, "alojamiento_desayuno", rng.choice(["web", "booking", "telefono"]), "Temporada", f"Bloque entre semana temporada objetivo {pct:.0%}.")
            cur += timedelta(days=1)

        # Bajada posterior a mediados de septiembre: aun hay algo de negocio, pero ya no temporada fuerte.
        cur = date(args.year, 9, 16)
        while cur < date(args.year, 11, 1):
            if cur.weekday() == 0:
                pct = rng.uniform(0.18, 0.35)
                target = round(len(all_rooms) * pct)
                pax = target * 2 + rng.randint(-3, 4)
                reserve_block(cur, cur + timedelta(days=3), pax, target, "media", "alojamiento_desayuno", rng.choice(["web", "booking", "telefono"]), "Post temporada", f"Bajada post verano objetivo {pct:.0%}.")
            cur += timedelta(days=1)

        # Fines de semana y sueltos.
        day = date(args.year, 1, 15)
        end_year = date(args.year, 12, 21)
        while day < end_year:
            if day.weekday() == 4:
                if ss_start <= day < ss_end:
                    day += timedelta(days=7)
                    continue
                if day < date(args.year, 6, 1):
                    pct = rng.uniform(0.50, 0.75) if day > ss_end else 0.75
                    segment = "media" if day > ss_end else "baja"
                elif day <= date(args.year, 9, 15):
                    pct = rng.uniform(0.72, 0.92)
                    segment = "alta"
                elif day < date(args.year, 11, 1):
                    pct = rng.uniform(0.40, 0.68)
                    segment = "media"
                else:
                    pct = rng.uniform(0.45, 0.65)
                    segment = "baja"
                target = max(0, round(len(all_rooms) * pct) - len([r for r in all_rooms if not available_rooms([r], occupied, day, day + timedelta(days=2))]))
                if target > 0:
                    pax = target * 2 + rng.randint(-3, 4)
                    reserve_block(day, day + timedelta(days=2), pax, target, segment, "alojamiento_desayuno", rng.choice(["web", "booking", "telefono"]), "Fin de semana", f"Fin de semana ocupacion objetivo {pct:.0%}.")
                day += timedelta(days=7)
                continue
            if day.weekday() in {1, 2, 3} and rng.random() < 0.26:
                avail = available_rooms(all_rooms, occupied, day, day + timedelta(days=1))
                if avail:
                    room = rng.choice(avail[: min(12, len(avail))])
                    rid = insert_reservation(
                        conn, args.empresa_id, args.centro_id, room, day, day + timedelta(days=1), 1 if room.capacidad == 1 else 2,
                        "alojamiento_desayuno", f"Cliente suelto {day.strftime('%d/%m')}", canales.get(rng.choice(["web", "booking", "telefono"])),
                        "baja", 1, "Cliente suelto entre semana."
                    )
                    restaurant_services += add_restaurant_services(conn, rid, args.empresa_id, args.centro_id, day, day + timedelta(days=1), 1 if room.capacidad == 1 else 2, "baja", pv_id)
                    mark(occupied, room.id, day, day + timedelta(days=1))
                    created += 1
            day += timedelta(days=1)

        # Resumen diario.
        for d in daterange(date(args.year, 1, 15), end_year):
            occ_rooms = 0
            pax = 0
            segment_counts: dict[str, int] = {}
            for row in conn.execute(
                """
                SELECT adultos, observaciones_operativas
                FROM reservas_recepcion
                WHERE empresa_id=? AND centro_id=? AND origen=? AND fecha_entrada <= ? AND fecha_salida > ?
                  AND estado NOT IN ('cancelada','no_show')
                """,
                (args.empresa_id, args.centro_id, ORIGEN, d.isoformat(), d.isoformat()),
            ).fetchall():
                occ_rooms += 1
                pax += int(row["adultos"] or 0)
                obs = row["observaciones_operativas"] or ""
                seg = "otros"
                for key in ["inserso", "semana_santa", "alta", "media", "baja"]:
                    if f"Segmento {key}" in obs:
                        seg = key
                        break
                segment_counts[seg] = segment_counts.get(seg, 0) + 1
            main_segment = max(segment_counts, key=segment_counts.get) if segment_counts else "sin_ocupacion"
            conn.execute(
                """
                INSERT INTO simulador_albarracin_ocupacion_resumen
                (empresa_id, centro_id, year, fecha, habitaciones_ocupadas,
                 habitaciones_total, ocupacion_pct, pax, segmento)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(empresa_id, centro_id, year, fecha)
                DO UPDATE SET habitaciones_ocupadas=excluded.habitaciones_ocupadas,
                    habitaciones_total=excluded.habitaciones_total,
                    ocupacion_pct=excluded.ocupacion_pct,
                    pax=excluded.pax,
                    segmento=excluded.segmento,
                    created_at=CURRENT_TIMESTAMP
                """,
                (
                    args.empresa_id,
                    args.centro_id,
                    args.year,
                    d.isoformat(),
                    occ_rooms,
                    len(all_rooms),
                    round(occ_rooms / len(all_rooms) * 100, 2),
                    pax,
                    main_segment,
                ),
            )

        conn.commit()
        print({
            "reservas_creadas": created,
            "servicios_restaurante": restaurant_services,
            "habitaciones": len(all_rooms),
            "desde": f"{args.year}-01-15",
            "hasta": f"{args.year}-12-20",
        })
    finally:
        conn.close()


if __name__ == "__main__":
    main()

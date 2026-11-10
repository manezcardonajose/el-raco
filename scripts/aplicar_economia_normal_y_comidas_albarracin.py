# -*- coding: utf-8 -*-
"""Ajusta cliente normal AD y simula comidas de fin de semana en Albarracin."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
EMPRESA_ID = 2
CENTRO_ID = 8
ORIGEN = "simulador_albarracin_ocupacion"


TARIFAS_PAX_NOCHE = {
    "baja": 60.0,
    "media": 60.0,
    "fin_semana": 70.0,
    "media_pension": 78.0,
    "alta": 85.0,
    "semana_santa": 85.0,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Economia normal y comidas finde Albarracin.")
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


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS albarracin_economia_segmento_diaria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER,
            centro_id INTEGER,
            year INTEGER,
            fecha TEXT,
            segmento TEXT,
            pax INTEGER DEFAULT 0,
            habitaciones INTEGER DEFAULT 0,
            ingreso_alojamiento_desayuno REAL DEFAULT 0,
            coste_desayuno REAL DEFAULT 0,
            coste_limpieza_lenceria REAL DEFAULT 0,
            coste_suministros REAL DEFAULT 0,
            coste_comision REAL DEFAULT 0,
            margen_operativo_estimado REAL DEFAULT 0,
            lectura TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, centro_id, year, fecha, segmento)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS albarracin_tarifas_cliente_normal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER,
            centro_id INTEGER,
            year INTEGER,
            temporada TEXT,
            regimen TEXT,
            precio_habitacion_noche REAL DEFAULT 0,
            precio_pax_noche REAL DEFAULT 0,
            fuente TEXT,
            observaciones TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, centro_id, year, temporada, regimen)
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(albarracin_tarifas_cliente_normal)").fetchall()}
    if "precio_pax_noche" not in cols:
        conn.execute("ALTER TABLE albarracin_tarifas_cliente_normal ADD COLUMN precio_pax_noche REAL DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS albarracin_oportunidad_comidas_finde (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER,
            centro_id INTEGER,
            year INTEGER,
            fecha TEXT,
            pax_base INTEGER DEFAULT 0,
            ocupacion_pct REAL DEFAULT 0,
            escenario TEXT,
            servicio TEXT,
            captacion_pct REAL DEFAULT 0,
            precio_pax REAL DEFAULT 0,
            coste_variable_pax REAL DEFAULT 0,
            coste_refuerzo REAL DEFAULT 0,
            pax_estimados INTEGER DEFAULT 0,
            ingresos REAL DEFAULT 0,
            coste_variable REAL DEFAULT 0,
            margen_estimado REAL DEFAULT 0,
            decision TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, centro_id, year, fecha, escenario, servicio)
        )
        """
    )


def segment_from_obs(obs: str) -> str | None:
    for segment in ["semana_santa", "alta", "media", "baja"]:
        if f"Segmento {segment}" in (obs or ""):
            return segment
    return None


def upsert_tarifas(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int) -> None:
    tarifas = [
        ("baja", "alojamiento_desayuno", TARIFAS_PAX_NOCHE["baja"], "Entre semana / temporada baja AD por pax/noche"),
        ("media", "alojamiento_desayuno", TARIFAS_PAX_NOCHE["media"], "Entre semana normal AD por pax/noche"),
        ("fin_semana", "alojamiento_desayuno", TARIFAS_PAX_NOCHE["fin_semana"], "Fin de semana AD por pax/noche"),
        ("fin_semana", "media_pension", TARIFAS_PAX_NOCHE["media_pension"], "Fin de semana media pension por pax/noche"),
        ("alta", "alojamiento_desayuno", TARIFAS_PAX_NOCHE["alta"], "Temporada alta AD por pax/noche"),
        ("semana_santa", "media_pension", TARIFAS_PAX_NOCHE["semana_santa"], "Semana Santa / alta demanda por pax/noche"),
    ]
    for temporada, regimen, precio, obs in tarifas:
        conn.execute(
            """
            INSERT INTO albarracin_tarifas_cliente_normal
            (empresa_id, centro_id, year, temporada, regimen, precio_habitacion_noche, precio_pax_noche, fuente, observaciones)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'usuario', ?)
            ON CONFLICT(empresa_id, centro_id, year, temporada, regimen)
            DO UPDATE SET precio_habitacion_noche=excluded.precio_habitacion_noche,
                precio_pax_noche=excluded.precio_pax_noche,
                fuente=excluded.fuente,
                observaciones=excluded.observaciones,
                updated_at=CURRENT_TIMESTAMP
            """,
            (empresa_id, centro_id, year, temporada, regimen, 0, precio, obs),
        )


def tarifa_reserva(segment: str, regimen: str, start: date, end: date) -> float:
    regimen = (regimen or "").lower()
    if regimen == "media_pension":
        return TARIFAS_PAX_NOCHE["media_pension"] if segment != "semana_santa" else TARIFAS_PAX_NOCHE["semana_santa"]
    if segment in {"alta", "semana_santa"}:
        return TARIFAS_PAX_NOCHE["alta"]
    if any(date.fromordinal(start.toordinal() + i).weekday() in {4, 5} for i in range(max((end - start).days, 1))):
        return TARIFAS_PAX_NOCHE["fin_semana"]
    return TARIFAS_PAX_NOCHE["baja"]


def update_normal_reservations(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> int:
    updated = 0
    rows = conn.execute(
        """
        SELECT id, fecha_entrada, fecha_salida, adultos, regimen, observaciones_operativas, canal_id
        FROM reservas_recepcion
        WHERE empresa_id=? AND centro_id=? AND origen=?
          AND observaciones_operativas NOT LIKE '%Segmento inserso%'
        """,
        (empresa_id, centro_id, ORIGEN),
    ).fetchall()
    for row in rows:
        segment = segment_from_obs(row["observaciones_operativas"])
        if not segment:
            continue
        start = date.fromisoformat(row["fecha_entrada"])
        end = date.fromisoformat(row["fecha_salida"])
        nights = max((end - start).days, 1)
        pax = max(int(row["adultos"] or 1), 1)
        total = round(tarifa_reserva(segment, row["regimen"], start, end) * pax * nights, 2)
        deposito = round(total * 0.15, 2)
        saldo = round(total - deposito, 2)
        conn.execute(
            """
            UPDATE reservas_recepcion
            SET precio_total=?, deposito=?, saldo=?, updated_at=CURRENT_TIMESTAMP,
                observaciones=TRIM(COALESCE(observaciones,'') || ' | Cliente normal recalculado por pax/noche segun tarifa Albarracin.')
            WHERE id=?
            """,
            (total, deposito, saldo, row["id"]),
        )
        updated += 1
    return updated


def rebuild_daily(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int) -> int:
    conn.execute(
        "DELETE FROM albarracin_economia_segmento_diaria WHERE empresa_id=? AND centro_id=? AND year=?",
        (empresa_id, centro_id, year),
    )
    rows = conn.execute(
        """
        SELECT r.id, r.fecha_entrada, r.fecha_salida, r.adultos, r.precio_total,
               r.comision_canal, r.observaciones_operativas
        FROM reservas_recepcion r
        WHERE r.empresa_id=? AND r.centro_id=? AND r.origen=?
          AND r.observaciones_operativas NOT LIKE '%Segmento inserso%'
          AND r.estado NOT IN ('cancelada','no_show')
        """,
        (empresa_id, centro_id, ORIGEN),
    ).fetchall()
    by_day: dict[tuple[str, str], dict] = {}
    for row in rows:
        segment = segment_from_obs(row["observaciones_operativas"])
        if not segment:
            continue
        start = date.fromisoformat(row["fecha_entrada"])
        end = date.fromisoformat(row["fecha_salida"])
        nights = max((end - start).days, 1)
        ingreso_dia = float(row["precio_total"] or 0) / nights
        comision_dia = ingreso_dia * float(row["comision_canal"] or 0) / 100
        cur = start
        while cur < end:
            key = (cur.isoformat(), segment)
            slot = by_day.setdefault(key, {"pax": 0, "habitaciones": 0, "ingreso": 0.0, "comision": 0.0})
            slot["pax"] += int(row["adultos"] or 0)
            slot["habitaciones"] += 1
            slot["ingreso"] += ingreso_dia
            slot["comision"] += comision_dia
            cur = date.fromordinal(cur.toordinal() + 1)
    inserted = 0
    for (fecha, segment), data in sorted(by_day.items()):
        pax = data["pax"]
        desayuno = round(pax * 4.20, 2)
        limpieza = round(data["habitaciones"] * 7.50, 2)
        suministros = round(pax * 3.00, 2)
        margen = round(data["ingreso"] - desayuno - limpieza - suministros - data["comision"], 2)
        lectura = "Cliente normal sano: margen operativo positivo." if margen > 0 else "Riesgo: revisar tarifa/coste operativo."
        conn.execute(
            """
            INSERT INTO albarracin_economia_segmento_diaria
            (empresa_id, centro_id, year, fecha, segmento, pax, habitaciones,
             ingreso_alojamiento_desayuno, coste_desayuno, coste_limpieza_lenceria,
             coste_suministros, coste_comision, margen_operativo_estimado, lectura)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                empresa_id,
                centro_id,
                year,
                fecha,
                segment,
                pax,
                data["habitaciones"],
                round(data["ingreso"], 2),
                desayuno,
                limpieza,
                suministros,
                round(data["comision"], 2),
                margen,
                lectura,
            ),
        )
        inserted += 1
    return inserted


def rebuild_weekend_meals(conn: sqlite3.Connection, empresa_id: int, centro_id: int, year: int) -> int:
    conn.execute(
        "DELETE FROM albarracin_oportunidad_comidas_finde WHERE empresa_id=? AND centro_id=? AND year=?",
        (empresa_id, centro_id, year),
    )
    scenarios = [
        ("prudente", "almuerzo", 0.18, 24.0, 9.0),
        ("prudente", "cena", 0.28, 28.0, 10.5),
        ("medio", "almuerzo", 0.28, 24.0, 9.0),
        ("medio", "cena", 0.42, 28.0, 10.5),
        ("fuerte", "almuerzo", 0.40, 24.0, 9.0),
        ("fuerte", "cena", 0.58, 28.0, 10.5),
    ]
    inserted = 0
    rows = conn.execute(
        """
        SELECT fecha, SUM(pax) pax, SUM(habitaciones) habitaciones
        FROM albarracin_economia_segmento_diaria
        WHERE empresa_id=? AND centro_id=? AND year=?
        GROUP BY fecha
        """,
        (empresa_id, centro_id, year),
    ).fetchall()
    total_rooms = conn.execute(
        "SELECT COUNT(*) FROM habitaciones_recepcion WHERE empresa_id=? AND centro_id=? AND COALESCE(activo,1)=1",
        (empresa_id, centro_id),
    ).fetchone()[0] or 41
    for row in rows:
        fecha = date.fromisoformat(row["fecha"])
        if fecha.weekday() not in {4, 5}:  # viernes/sabado
            continue
        pax_base = int(row["pax"] or 0)
        occ = float(row["habitaciones"] or 0) / total_rooms * 100
        for escenario, servicio, cap_pct, precio, coste in scenarios:
            pax_est = round(pax_base * cap_pct)
            coste_refuerzo = 160.0 if pax_est >= 18 else 0.0
            ingresos = round(pax_est * precio, 2)
            variable = round(pax_est * coste, 2)
            margen = round(ingresos - variable - coste_refuerzo, 2)
            if pax_est < 12:
                decision = "NO ABRIR: poca demanda estimada."
            elif margen < 80:
                decision = "DUDOSO: solo con reserva previa o menu cerrado."
            else:
                decision = "VIABLE: ofrecer con cupo y reserva previa."
            conn.execute(
                """
                INSERT INTO albarracin_oportunidad_comidas_finde
                (empresa_id, centro_id, year, fecha, pax_base, ocupacion_pct,
                 escenario, servicio, captacion_pct, precio_pax, coste_variable_pax,
                 coste_refuerzo, pax_estimados, ingresos, coste_variable,
                 margen_estimado, decision)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    empresa_id,
                    centro_id,
                    year,
                    row["fecha"],
                    pax_base,
                    round(occ, 2),
                    escenario,
                    servicio,
                    cap_pct * 100,
                    precio,
                    coste,
                    coste_refuerzo,
                    pax_est,
                    ingresos,
                    variable,
                    margen,
                    decision,
                ),
            )
            inserted += 1
    return inserted


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        upsert_tarifas(conn, args.empresa_id, args.centro_id, args.year)
        updated = update_normal_reservations(conn, args.empresa_id, args.centro_id)
        days = rebuild_daily(conn, args.empresa_id, args.centro_id, args.year)
        meal_rows = rebuild_weekend_meals(conn, args.empresa_id, args.centro_id, args.year)
        conn.commit()
        totals = conn.execute(
            """
            SELECT ROUND(SUM(ingreso_alojamiento_desayuno),2),
                   ROUND(SUM(coste_desayuno),2),
                   ROUND(SUM(margen_operativo_estimado),2)
            FROM albarracin_economia_segmento_diaria
            WHERE empresa_id=? AND centro_id=? AND year=?
            """,
            (args.empresa_id, args.centro_id, args.year),
        ).fetchone()
        viable = conn.execute(
            """
            SELECT COUNT(*) FROM albarracin_oportunidad_comidas_finde
            WHERE empresa_id=? AND centro_id=? AND year=? AND decision LIKE 'VIABLE%'
            """,
            (args.empresa_id, args.centro_id, args.year),
        ).fetchone()[0]
        print({
            "reservas_normales_actualizadas": updated,
            "dias_segmento": days,
            "escenarios_comidas_finde": meal_rows,
            "escenarios_viables": viable,
            "ingreso_ad": totals[0] or 0,
            "coste_desayuno": totals[1] or 0,
            "margen_operativo": totals[2] or 0,
        })
    finally:
        conn.close()


if __name__ == "__main__":
    main()

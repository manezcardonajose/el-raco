# -*- coding: utf-8 -*-
"""Gestiona la cola diaria de cocina/mise y mantenimiento del simulador.

No borra datos. Convierte alertas y ordenes abiertas en una lectura operativa:
que se puede completar con la capacidad del dia, que queda bloqueado por stock
y que se queda pendiente por falta de manos.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3

PRIORIDAD = {"critica": 1, "urgente": 1, "alta": 2, "media": 3, "baja": 4, "info": 5}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gestionar jornada operativa simulada.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--desde", default="")
    parser.add_argument("--hasta", default="")
    parser.add_argument("--capacidad-mise", type=int, default=38)
    parser.add_argument("--capacidad-mantenimiento", type=int, default=6)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def latest_corrida(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "simulador_vida_corridas"):
        return 0
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_jornada_operativa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            fecha TEXT NOT NULL,
            area TEXT NOT NULL,
            entrada INTEGER DEFAULT 0,
            completadas INTEGER DEFAULT 0,
            bloqueadas_stock INTEGER DEFAULT 0,
            bloqueadas_capacidad INTEGER DEFAULT 0,
            pendientes_final INTEGER DEFAULT 0,
            capacidad INTEGER DEFAULT 0,
            riesgo TEXT,
            lectura TEXT,
            accion_direccion TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, fecha, area)
        )
        """
    )


def parse_day(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def simulation_range(conn: sqlite3.Connection, corrida_id: int, desde: str, hasta: str) -> tuple[date, date]:
    if desde and hasta:
        return parse_day(desde), parse_day(hasta)
    row = conn.execute(
        """
        SELECT MIN(fecha) min_fecha, MAX(fecha) max_fecha
        FROM (
            SELECT fecha_servicio AS fecha FROM simulador_alertas_mise_en_place WHERE corrida_id=?
            UNION ALL
            SELECT substr(fecha_apertura,1,10) AS fecha FROM ordenes_trabajo WHERE numero_orden LIKE 'OT-VIDA-H1-%'
            UNION ALL
            SELECT fecha FROM simulador_vida_kpis_diarios WHERE corrida_id=?
        )
        WHERE fecha IS NOT NULL AND fecha <> ''
        """,
        (corrida_id, corrida_id),
    ).fetchone()
    start = parse_day(desde or row["min_fecha"] or "2026-06-01")
    end = parse_day(hasta or row["max_fecha"] or row["min_fecha"] or "2026-06-01")
    return start, end


def riesgo_por_pendientes(total: int, bloqueadas_stock: int, bloqueadas_capacidad: int) -> str:
    if bloqueadas_stock or bloqueadas_capacidad > 8:
        return "critica"
    if total and bloqueadas_capacidad > 0:
        return "alta"
    if total:
        return "controlada"
    return "sin_carga"


def gestionar_mise(conn: sqlite3.Connection, corrida_id: int, centro_id: int, fecha: date, capacidad: int) -> dict:
    if not table_exists(conn, "simulador_alertas_mise_en_place"):
        return {"entrada": 0, "completadas": 0, "stock": 0, "capacidad": 0}
    rows = conn.execute(
        """
        SELECT a.*, COALESCE(p.cantidad, 0) AS stock_actual
        FROM simulador_alertas_mise_en_place a
        LEFT JOIN productos p ON p.id=a.producto_id
        WHERE a.corrida_id=? AND a.centro_id=? AND a.fecha_servicio=?
          AND COALESCE(a.estado,'pendiente') NOT IN ('completada','cancelada')
        ORDER BY CASE prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END,
                 fecha_alerta, id
        """,
        (corrida_id, centro_id, fecha.isoformat()),
    ).fetchall()
    completadas = bloqueadas_stock = bloqueadas_capacidad = 0
    usados = 0
    for row in rows:
        accion = (row["accion"] or "").lower()
        cantidad_necesaria = float(row["cantidad"] or 0)
        stock_actual = float(row["stock_actual"] or 0)
        falta_stock_real = bool(row["producto_id"]) and cantidad_necesaria > 0 and stock_actual < cantidad_necesaria
        if ("falta stock" in accion or "pedir o buscar alternativa" in accion) and falta_stock_real:
            estado = "bloqueada_stock"
            bloqueadas_stock += 1
        elif usados < capacidad:
            estado = "completada"
            completadas += 1
            usados += 1
        else:
            estado = "bloqueada_capacidad"
            bloqueadas_capacidad += 1
        conn.execute(
            """
            UPDATE simulador_alertas_mise_en_place
            SET estado=?
            WHERE id=?
            """,
            (estado, row["id"]),
        )
    return {
        "entrada": len(rows),
        "completadas": completadas,
        "stock": bloqueadas_stock,
        "capacidad": bloqueadas_capacidad,
    }


def gestionar_mantenimiento(conn: sqlite3.Connection, corrida_id: int, centro_id: int, fecha: date, capacidad: int) -> dict:
    if not table_exists(conn, "ordenes_trabajo"):
        return {"entrada": 0, "completadas": 0, "stock": 0, "capacidad": 0}
    rows = conn.execute(
        """
        SELECT *
        FROM ordenes_trabajo
        WHERE (numero_orden LIKE 'OT-VIDA-H1-%' OR COALESCE(origen_modulo,'')='simulador_vida')
          AND date(fecha_apertura)=date(?)
          AND COALESCE(estado,'') NOT IN ('cerrada','cerrado','finalizada','completada')
        ORDER BY CASE prioridad WHEN 'urgente' THEN 1 WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END,
                 fecha_apertura, id
        """,
        (fecha.isoformat(),),
    ).fetchall()
    completadas = bloqueadas_capacidad = 0
    for idx, row in enumerate(rows):
        if idx < capacidad:
            cierre = f"{fecha.isoformat()} 18:30:00"
            conn.execute(
                """
                UPDATE ordenes_trabajo
                SET estado='completada',
                    fecha_inicio=COALESCE(fecha_inicio, ?),
                    fecha_fin=COALESCE(fecha_fin, ?),
                    fecha_cierre=COALESCE(fecha_cierre, ?),
                    cerrado_por=COALESCE(cerrado_por, 'Jornada operativa simulada'),
                    horas_reales=COALESCE(horas_reales, horas_estimadas, 1),
                    coste_real=COALESCE(coste_real, coste_estimado, 0),
                    trabajo_realizado=COALESCE(trabajo_realizado, 'Resuelto dentro de la jornada operativa simulada'),
                    resultado=COALESCE(resultado, 'operativo')
                WHERE id=?
                """,
                (f"{fecha.isoformat()} 09:00:00", cierre, cierre, row["id"]),
            )
            completadas += 1
        else:
            conn.execute(
                """
                UPDATE ordenes_trabajo
                SET estado='pendiente_capacidad',
                    observaciones=COALESCE(observaciones, 'Pendiente por capacidad de mantenimiento simulada')
                WHERE id=?
                """,
                (row["id"],),
            )
            bloqueadas_capacidad += 1
    return {"entrada": len(rows), "completadas": completadas, "stock": 0, "capacidad": bloqueadas_capacidad}


def upsert_jornada(
    conn: sqlite3.Connection,
    corrida_id: int,
    centro_id: int,
    fecha: date,
    area: str,
    datos: dict,
    capacidad: int,
) -> None:
    pendientes = int(datos["stock"] + datos["capacidad"])
    riesgo = riesgo_por_pendientes(int(datos["entrada"]), int(datos["stock"]), int(datos["capacidad"]))
    if area == "cocina_mise":
        if datos["stock"]:
            lectura = "Mise bloqueada por falta de stock: cocina no puede ejecutar todo lo planificado."
            accion = "Generar pedido urgente, ajustar menu o sustituir producto antes del servicio."
        elif datos["capacidad"]:
            lectura = "Mise bloqueada por capacidad: faltan manos u horas antes del servicio."
            accion = "Reordenar prioridades, reforzar partida o simplificar elaboraciones."
        else:
            lectura = "Mise ejecutada dentro de la capacidad prevista."
            accion = "Mantener control de temperaturas y reposicion."
    else:
        if datos["capacidad"]:
            lectura = "Mantenimiento no absorbe toda la carga del dia."
            accion = "Priorizar habitaciones vendibles, agua, ascensores, piscina y climatizacion."
        elif datos["entrada"]:
            lectura = "Mantenimiento resuelve la carga simulada del dia."
            accion = "Registrar coste real y comprobar recurrencias."
        else:
            lectura = "Sin carga nueva de mantenimiento en la jornada."
            accion = "Aprovechar para preventivo."
    conn.execute(
        """
        INSERT INTO simulador_jornada_operativa
        (corrida_id, centro_id, fecha, area, entrada, completadas,
         bloqueadas_stock, bloqueadas_capacidad, pendientes_final, capacidad,
         riesgo, lectura, accion_direccion, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(corrida_id, centro_id, fecha, area) DO UPDATE SET
            entrada=excluded.entrada,
            completadas=excluded.completadas,
            bloqueadas_stock=excluded.bloqueadas_stock,
            bloqueadas_capacidad=excluded.bloqueadas_capacidad,
            pendientes_final=excluded.pendientes_final,
            capacidad=excluded.capacidad,
            riesgo=excluded.riesgo,
            lectura=excluded.lectura,
            accion_direccion=excluded.accion_direccion
        """,
        (
            corrida_id,
            centro_id,
            fecha.isoformat(),
            area,
            int(datos["entrada"]),
            int(datos["completadas"]),
            int(datos["stock"]),
            int(datos["capacidad"]),
            pendientes,
            capacidad,
            riesgo,
            lectura,
            accion,
            EMPRESA_ID,
        ),
    )


def ejecutar(conn: sqlite3.Connection, corrida_id: int, centro_id: int, desde: str, hasta: str, cap_mise: int, cap_mant: int) -> dict:
    ensure_schema(conn)
    start, end = simulation_range(conn, corrida_id, desde, hasta)
    dias = 0
    for day in date_range(start, end):
        mise = gestionar_mise(conn, corrida_id, centro_id, day, cap_mise)
        mant = gestionar_mantenimiento(conn, corrida_id, centro_id, day, cap_mant)
        upsert_jornada(conn, corrida_id, centro_id, day, "cocina_mise", mise, cap_mise)
        upsert_jornada(conn, corrida_id, centro_id, day, "mantenimiento", mant, cap_mant)
        dias += 1
    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) filas,
               SUM(entrada) entrada,
               SUM(completadas) completadas,
               SUM(bloqueadas_stock) bloqueadas_stock,
               SUM(bloqueadas_capacidad) bloqueadas_capacidad,
               SUM(pendientes_final) pendientes_final,
               SUM(CASE WHEN riesgo='critica' THEN 1 ELSE 0 END) criticas,
               SUM(CASE WHEN riesgo='alta' THEN 1 ELSE 0 END) altas
        FROM simulador_jornada_operativa
        WHERE corrida_id=? AND centro_id=? AND fecha BETWEEN ? AND ?
        """,
        (corrida_id, centro_id, start.isoformat(), end.isoformat()),
    ).fetchone()
    return dict(resumen) | {"dias": dias, "desde": start.isoformat(), "hasta": end.isoformat()}


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida de simulacion.")
        print(ejecutar(conn, corrida_id, args.centro_id, args.desde, args.hasta, args.capacidad_mise, args.capacidad_mantenimiento))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

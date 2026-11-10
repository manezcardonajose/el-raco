# -*- coding: utf-8 -*-
"""Crea prevencion y propuestas de resolucion para overbooking simulado.

No borra reservas ni reasigna habitaciones reales: deja una capa trazable para
ver que habria que cerrar, abrir o desviar antes de que el cliente llegue.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gestion preventiva de overbooking en simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--colchon", type=int, default=2, help="Habitaciones que no se deben abrir a canales externos.")
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


def latest_corrida(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_hoteles_apoyo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            zona TEXT,
            distancia_km REAL,
            categoria TEXT,
            habitaciones_apoyo INTEGER DEFAULT 0,
            coste_desvio REAL DEFAULT 0,
            prioridad INTEGER DEFAULT 10,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            centro_origen_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(nombre, centro_origen_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_overbooking_politicas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            centro_id INTEGER,
            canal_id INTEGER,
            tipo_habitacion_id INTEGER,
            colchon_habitaciones INTEGER DEFAULT 2,
            release_dias INTEGER DEFAULT 3,
            permite_sobreventa INTEGER DEFAULT 0,
            max_sobreventa INTEGER DEFAULT 0,
            accion_si_riesgo TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(centro_id, canal_id, tipo_habitacion_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_overbooking_prevencion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha TEXT,
            centro_id INTEGER,
            canal_id INTEGER,
            canal_codigo TEXT,
            canal_tipo TEXT,
            tipo_habitacion_id INTEGER,
            habitaciones_totales INTEGER,
            bloqueadas INTEGER,
            reservas_activas INTEGER,
            cupo_canal INTEGER,
            colchon_habitaciones INTEGER,
            habitaciones_disponibles_reales INTEGER,
            venta_maxima_recomendada INTEGER,
            riesgo TEXT,
            accion_recomendada TEXT,
            evidencia_json TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(corrida_id, fecha, centro_id, canal_id, tipo_habitacion_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_overbooking_resoluciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            caso_id INTEGER,
            estrategia TEXT,
            prioridad TEXT,
            reserva_id INTEGER,
            reserva_conflicto_id INTEGER,
            habitacion_origen_id INTEGER,
            habitacion_destino_id INTEGER,
            centro_destino_id INTEGER,
            hotel_apoyo_id INTEGER,
            coste_estimado REAL,
            estado TEXT DEFAULT 'propuesta',
            pasos_json TEXT,
            evidencia_json TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(caso_id)
        )
        """
    )


def seed_support_hotels(conn: sqlite3.Connection) -> None:
    hoteles = [
        ("Hotel Apoyo Playa Norte", "misma zona", 0.8, "4*", 8, 95, 1),
        ("Hotel Apoyo Centro", "centro urbano", 2.4, "3* sup", 12, 75, 2),
        ("Apartahotel Familiar Costa", "zona familiar", 3.1, "3*", 6, 65, 3),
    ]
    for row in hoteles:
        conn.execute(
            """
            INSERT INTO simulador_hoteles_apoyo
            (nombre, zona, distancia_km, categoria, habitaciones_apoyo, coste_desvio,
             prioridad, activo, empresa_id, centro_origen_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 3)
            ON CONFLICT(nombre, centro_origen_id) DO UPDATE SET
                zona=excluded.zona,
                distancia_km=excluded.distancia_km,
                categoria=excluded.categoria,
                habitaciones_apoyo=excluded.habitaciones_apoyo,
                coste_desvio=excluded.coste_desvio,
                prioridad=excluded.prioridad,
                activo=1
            """,
            row,
        )


def seed_policies(conn: sqlite3.Connection, default_colchon: int) -> int:
    canales = conn.execute(
        """
        SELECT id, codigo, tipo
        FROM recepcion_canales_reserva
        WHERE empresa_id=? AND COALESCE(activo, 1)=1
        """,
        (EMPRESA_ID,),
    ).fetchall()
    tipos = conn.execute(
        """
        SELECT DISTINCT tipo_habitacion_id
        FROM habitaciones_recepcion
        WHERE empresa_id=? AND centro_id=? AND activo=1 AND tipo_habitacion_id IS NOT NULL
        """,
        (EMPRESA_ID, CENTRO_ID),
    ).fetchall()
    created = 0
    for canal in canales:
        tipo_canal = (canal["tipo"] or "").lower()
        if tipo_canal in {"directo"}:
            colchon, release, permite, max_sobreventa = 1, 1, 0, 0
        elif tipo_canal in {"touroperador", "tour_operador"} or "tour" in (canal["codigo"] or "").lower():
            colchon, release, permite, max_sobreventa = 0, 7, 0, 0
        else:
            colchon, release, permite, max_sobreventa = default_colchon, 3, 0, 0
        for tipo in tipos:
            conn.execute(
                """
                INSERT INTO simulador_overbooking_politicas
                (centro_id, canal_id, tipo_habitacion_id, colchon_habitaciones,
                 release_dias, permite_sobreventa, max_sobreventa, accion_si_riesgo,
                 activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
                ON CONFLICT(centro_id, canal_id, tipo_habitacion_id) DO UPDATE SET
                    colchon_habitaciones=excluded.colchon_habitaciones,
                    release_dias=excluded.release_dias,
                    permite_sobreventa=excluded.permite_sobreventa,
                    max_sobreventa=excluded.max_sobreventa,
                    accion_si_riesgo=excluded.accion_si_riesgo,
                    activo=1,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    CENTRO_ID,
                    canal["id"],
                    tipo["tipo_habitacion_id"],
                    colchon,
                    release,
                    permite,
                    max_sobreventa,
                    "cerrar_canal_y_revisar_asignacion" if colchon else "vigilar_cupo_contrato",
                ),
            )
            created += 1
    return created


def overlap_where(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"{prefix}fecha_entrada < ? AND {prefix}fecha_salida > ?"


def room_is_free(conn: sqlite3.Connection, room_id: int, entrada: str, salida: str, exclude_reserva_ids: tuple[int, ...] = ()) -> bool:
    params: list[object] = [room_id, salida, entrada]
    extra = ""
    if exclude_reserva_ids:
        extra = f" AND id NOT IN ({','.join('?' for _ in exclude_reserva_ids)})"
        params.extend(exclude_reserva_ids)
    row = conn.execute(
        f"""
        SELECT COUNT(*) total
        FROM reservas_recepcion
        WHERE habitacion_id=?
          AND estado NOT IN ('cancelada','checkout','no_show')
          AND {overlap_where()}
          {extra}
        """,
        params,
    ).fetchone()
    if int(row["total"] or 0) > 0:
        return False
    block = conn.execute(
        """
        SELECT COUNT(*) total
        FROM bloqueos_habitacion_recepcion
        WHERE habitacion_id=? AND estado='activo'
          AND fecha_inicio < ? AND fecha_fin > ?
        """,
        (room_id, salida, entrada),
    ).fetchone()
    return int(block["total"] or 0) == 0


def find_room(
    conn: sqlite3.Connection,
    entrada: str,
    salida: str,
    tipo_habitacion_id: int | None,
    pax: int,
    centro_id: int,
    allow_upgrade: bool,
    exclude_reserva_ids: tuple[int, ...],
) -> sqlite3.Row | None:
    params: list[object] = [EMPRESA_ID, centro_id, pax]
    type_clause = ""
    if tipo_habitacion_id and not allow_upgrade:
        type_clause = "AND tipo_habitacion_id=?"
        params.append(tipo_habitacion_id)
    elif tipo_habitacion_id and allow_upgrade:
        type_clause = "AND (tipo_habitacion_id=? OR capacidad>=?)"
        params.extend([tipo_habitacion_id, pax])
    rooms = conn.execute(
        f"""
        SELECT *
        FROM habitaciones_recepcion
        WHERE empresa_id=? AND centro_id=? AND activo=1 AND capacidad>=?
          {type_clause}
        ORDER BY CASE WHEN tipo_habitacion_id=? THEN 0 ELSE 1 END, capacidad, numero
        """,
        params + [tipo_habitacion_id or 0],
    ).fetchall()
    for room in rooms:
        if room_is_free(conn, int(room["id"]), entrada, salida, exclude_reserva_ids):
            return room
    return None


def count_blocked(conn: sqlite3.Connection, fecha: str, tipo_habitacion_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) total
        FROM bloqueos_habitacion_recepcion b
        JOIN habitaciones_recepcion h ON h.id=b.habitacion_id
        WHERE h.centro_id=? AND h.tipo_habitacion_id=? AND b.estado='activo'
          AND b.fecha_inicio <= ? AND b.fecha_fin > ?
        """,
        (CENTRO_ID, tipo_habitacion_id, fecha, fecha),
    ).fetchone()
    return int(row["total"] or 0)


def count_reservas(conn: sqlite3.Connection, fecha: str, tipo_habitacion_id: int, canal_id: int | None = None) -> int:
    params: list[object] = [CENTRO_ID, tipo_habitacion_id, fecha, fecha]
    canal_clause = ""
    if canal_id:
        canal_clause = "AND r.canal_id=?"
        params.append(canal_id)
    row = conn.execute(
        f"""
        SELECT COUNT(*) total
        FROM reservas_recepcion r
        WHERE r.centro_id=? AND r.tipo_habitacion_id=?
          AND r.estado NOT IN ('cancelada','checkout','no_show')
          AND r.fecha_entrada <= ? AND r.fecha_salida > ?
          {canal_clause}
        """,
        params,
    ).fetchone()
    return int(row["total"] or 0)


def create_prevention(conn: sqlite3.Connection, corrida_id: int) -> int:
    fechas = conn.execute(
        """
        SELECT DISTINCT fecha_entrada AS fecha
        FROM simulador_overbooking_casos
        WHERE corrida_id=?
        ORDER BY fecha_entrada
        """,
        (corrida_id,),
    ).fetchall()
    policies = conn.execute(
        """
        SELECT p.*, c.codigo AS canal_codigo, c.tipo AS canal_tipo
        FROM simulador_overbooking_politicas p
        JOIN recepcion_canales_reserva c ON c.id=p.canal_id
        WHERE p.centro_id=? AND p.activo=1
        """,
        (CENTRO_ID,),
    ).fetchall()
    created = 0
    for fecha_row in fechas:
        fecha = fecha_row["fecha"]
        for policy in policies:
            tipo_id = int(policy["tipo_habitacion_id"])
            total = int(conn.execute(
                """
                SELECT COUNT(*) total
                FROM habitaciones_recepcion
                WHERE empresa_id=? AND centro_id=? AND activo=1 AND tipo_habitacion_id=?
                """,
                (EMPRESA_ID, CENTRO_ID, tipo_id),
            ).fetchone()["total"] or 0)
            blocked = count_blocked(conn, fecha, tipo_id)
            active = count_reservas(conn, fecha, tipo_id)
            canal_sold = count_reservas(conn, fecha, tipo_id, int(policy["canal_id"]))
            disponible = max(0, total - blocked - active)
            colchon = int(policy["colchon_habitaciones"] or 0)
            sobreventa = int(policy["max_sobreventa"] or 0) if int(policy["permite_sobreventa"] or 0) else 0
            recommended = max(0, total - blocked - active - colchon + sobreventa)
            if disponible <= 0:
                riesgo = "critico"
                action = "Cerrar venta externa. Revisar asignaciones, cupos y habitaciones bloqueadas antes de aceptar mas reservas."
            elif recommended <= 0:
                riesgo = "alto"
                action = "Mantener solo venta directa controlada. Cerrar OTA y pedir confirmacion manual a operadores."
            elif canal_sold > max(0, total - colchon):
                riesgo = "alto"
                action = "Canal supera cupo seguro. Reducir inventario enviado y revisar sincronizacion."
            else:
                riesgo = "controlado"
                action = "Mantener cupo abierto con sincronizacion y release activo."
            conn.execute(
                """
                INSERT INTO simulador_overbooking_prevencion
                (corrida_id, fecha, centro_id, canal_id, canal_codigo, canal_tipo,
                 tipo_habitacion_id, habitaciones_totales, bloqueadas, reservas_activas,
                 cupo_canal, colchon_habitaciones, habitaciones_disponibles_reales,
                 venta_maxima_recomendada, riesgo, accion_recomendada, evidencia_json,
                 empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(corrida_id, fecha, centro_id, canal_id, tipo_habitacion_id) DO UPDATE SET
                    habitaciones_totales=excluded.habitaciones_totales,
                    bloqueadas=excluded.bloqueadas,
                    reservas_activas=excluded.reservas_activas,
                    cupo_canal=excluded.cupo_canal,
                    colchon_habitaciones=excluded.colchon_habitaciones,
                    habitaciones_disponibles_reales=excluded.habitaciones_disponibles_reales,
                    venta_maxima_recomendada=excluded.venta_maxima_recomendada,
                    riesgo=excluded.riesgo,
                    accion_recomendada=excluded.accion_recomendada,
                    evidencia_json=excluded.evidencia_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    corrida_id,
                    fecha,
                    CENTRO_ID,
                    policy["canal_id"],
                    policy["canal_codigo"],
                    policy["canal_tipo"],
                    tipo_id,
                    total,
                    blocked,
                    active,
                    canal_sold,
                    colchon,
                    disponible,
                    recommended,
                    riesgo,
                    action,
                    json.dumps({"release_dias": policy["release_dias"], "permite_sobreventa": policy["permite_sobreventa"]}, ensure_ascii=False),
                ),
            )
            created += 1
    return created


def support_hotel(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM simulador_hoteles_apoyo
        WHERE activo=1 AND habitaciones_apoyo>0 AND centro_origen_id=?
        ORDER BY prioridad, coste_desvio, distancia_km
        LIMIT 1
        """,
        (CENTRO_ID,),
    ).fetchone()


def resolve_cases(conn: sqlite3.Connection, corrida_id: int) -> int:
    if not table_exists(conn, "simulador_overbooking_casos"):
        return 0
    cases = conn.execute(
        """
        SELECT ob.*, r.tipo_habitacion_id, r.adultos, r.menores, r.precio_total,
               r.fecha_entrada AS r_entrada, r.fecha_salida AS r_salida
        FROM simulador_overbooking_casos ob
        LEFT JOIN reservas_recepcion r ON r.id=ob.reserva_id
        WHERE ob.corrida_id=?
        ORDER BY CASE ob.severidad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                 ob.fecha_entrada
        """,
        (corrida_id,),
    ).fetchall()
    created = 0
    for case in cases:
        entrada = case["fecha_entrada"] or case["r_entrada"]
        salida = case["fecha_salida"] or case["r_salida"]
        pax = int(case["pax_afectados"] or (case["adultos"] or 0) + (case["menores"] or 0) or 2)
        tipo_id = case["tipo_habitacion_id"]
        reserva_ids = tuple(
            int(x) for x in [case["reserva_id"], case["reserva_conflicto_id"]] if x
        )
        same_room = find_room(conn, entrada, salida, tipo_id, pax, CENTRO_ID, False, reserva_ids)
        upgrade_room = None if same_room else find_room(conn, entrada, salida, tipo_id, pax, CENTRO_ID, True, reserva_ids)
        chain_room = None
        if not same_room and not upgrade_room:
            for centro in conn.execute(
                "SELECT id FROM centros_trabajo WHERE empresa_id=? AND activo=1 AND id<>? ORDER BY id",
                (EMPRESA_ID, CENTRO_ID),
            ).fetchall():
                chain_room = find_room(conn, entrada, salida, tipo_id, pax, int(centro["id"]), True, reserva_ids)
                if chain_room:
                    break
        apoyo = None if same_room or upgrade_room or chain_room else support_hotel(conn)
        if same_room:
            estrategia = "reasignacion_interna"
            prioridad = "alta"
            coste = 0
            destino_room = same_room["id"]
            destino_centro = CENTRO_ID
            hotel_apoyo_id = None
            pasos = [
                "Mover una de las reservas a la habitacion libre del mismo tipo.",
                "Enviar inventario actualizado a todos los canales.",
                "Bloquear la habitacion original hasta cerrar el conflicto.",
            ]
        elif upgrade_room:
            estrategia = "upgrade_interno"
            prioridad = "alta"
            coste = 45
            destino_room = upgrade_room["id"]
            destino_centro = CENTRO_ID
            hotel_apoyo_id = None
            pasos = [
                "Aplicar upgrade sin coste al cliente afectado.",
                "Registrar compensacion y motivo en recepcion.",
                "Cerrar venta externa de ese tipo hasta sincronizar.",
            ]
        elif chain_room:
            estrategia = "desvio_cadena"
            prioridad = "critica"
            coste = 75
            destino_room = chain_room["id"]
            destino_centro = chain_room["centro_id"]
            hotel_apoyo_id = None
            pasos = [
                "Confirmar disponibilidad en hotel de la misma cadena.",
                "Trasladar reserva manteniendo regimen y precio pactado.",
                "Asumir transporte o detalle comercial si procede.",
            ]
        elif apoyo:
            estrategia = "hotel_apoyo_externo"
            prioridad = "critica"
            coste = float(apoyo["coste_desvio"] or 120)
            destino_room = None
            destino_centro = None
            hotel_apoyo_id = apoyo["id"]
            pasos = [
                "Llamar hotel de apoyo y bloquear habitacion antes de contactar al cliente.",
                "Confirmar por escrito condiciones, transporte y compensacion.",
                "Cerrar ventas de canales externos para la fecha afectada.",
            ]
        else:
            estrategia = "pendiente_direccion"
            prioridad = "critica"
            coste = float(case["coste_estimado"] or 180)
            destino_room = None
            destino_centro = None
            hotel_apoyo_id = None
            pasos = [
                "Escalar a direccion.",
                "Cerrar canales externos de inmediato.",
                "Buscar alternativa manual y registrar coste real.",
            ]
        estado_resolucion = "pendiente_direccion" if estrategia == "pendiente_direccion" else "ejecutada_simulada"
        estado_caso = "pendiente" if estrategia == "pendiente_direccion" else "resuelto_simulado"
        conn.execute(
            """
            INSERT INTO simulador_overbooking_resoluciones
            (corrida_id, caso_id, estrategia, prioridad, reserva_id, reserva_conflicto_id,
             habitacion_origen_id, habitacion_destino_id, centro_destino_id, hotel_apoyo_id,
             coste_estimado, estado, pasos_json, evidencia_json, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 3)
            ON CONFLICT(caso_id) DO UPDATE SET
                estrategia=excluded.estrategia,
                prioridad=excluded.prioridad,
                habitacion_destino_id=excluded.habitacion_destino_id,
                centro_destino_id=excluded.centro_destino_id,
                hotel_apoyo_id=excluded.hotel_apoyo_id,
                coste_estimado=excluded.coste_estimado,
                estado=excluded.estado,
                pasos_json=excluded.pasos_json,
                evidencia_json=excluded.evidencia_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                corrida_id,
                case["id"],
                estrategia,
                prioridad,
                case["reserva_id"],
                case["reserva_conflicto_id"],
                case["habitacion_id"],
                destino_room,
                destino_centro,
                hotel_apoyo_id,
                coste,
                estado_resolucion,
                json.dumps(pasos, ensure_ascii=False),
                json.dumps({"pax": pax, "tipo_habitacion_id": tipo_id, "entrada": entrada, "salida": salida}, ensure_ascii=False),
            ),
        )
        conn.execute(
            """
            UPDATE simulador_overbooking_casos
            SET decision_recomendada=?, resultado_simulado=?, estado_gestion=?
            WHERE id=?
            """,
            ("; ".join(pasos), estrategia, estado_caso, case["id"]),
        )
        created += 1
    return created


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        seed_support_hotels(conn)
        policies = seed_policies(conn, args.colchon)
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida de simulacion.")
        prevention = create_prevention(conn, corrida_id)
        resolutions = resolve_cases(conn, corrida_id)
        conn.commit()
        print(
            f"Gestion overbooking lista: {policies} politicas, "
            f"{prevention} controles preventivos, {resolutions} resoluciones propuestas."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()

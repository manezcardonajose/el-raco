# -*- coding: utf-8 -*-
"""Audita trazabilidad cruzada de la vida operativa simulada.

No borra ni corrige datos operativos. Deja un informe propio para saber si lo
generado por el simulador esta conectado entre modulos: reservas, pagos,
produccion, APPCC, compras, RRHH, TPV y mantenimiento.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auditar trazabilidad operativa de la simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=3)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def scalar(conn: sqlite3.Connection, sql: str, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        return row[0] if row[0] is not None else default
    except Exception:
        return default


def latest_corrida(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "simulador_vida_corridas"):
        return 0
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_trazabilidad_operativa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            area TEXT,
            severidad TEXT,
            codigo TEXT,
            titulo TEXT,
            detalle TEXT,
            evidencia_json TEXT,
            estado TEXT DEFAULT 'abierta',
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_trazabilidad_resumen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            total INTEGER DEFAULT 0,
            criticas INTEGER DEFAULT 0,
            altas INTEGER DEFAULT 0,
            medias INTEGER DEFAULT 0,
            bajas INTEGER DEFAULT 0,
            score REAL DEFAULT 0,
            lectura TEXT,
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def add(issues: list[dict], area: str, severidad: str, codigo: str, titulo: str, detalle: str, evidencia=None) -> None:
    issues.append(
        {
            "area": area,
            "severidad": severidad,
            "codigo": codigo,
            "titulo": titulo,
            "detalle": detalle,
            "evidencia": evidencia or {},
        }
    )


def reservas_where(centro_id: int) -> str:
    return (
        "empresa_id=1 AND (centro_id={centro_id} OR codigo LIKE 'VIDA-H1-%') "
        "AND COALESCE(estado,'') NOT IN ('cancelada','cancelado','no_show')"
    ).format(centro_id=int(centro_id))


def audit_recepcion(conn: sqlite3.Connection, centro_id: int, issues: list[dict]) -> None:
    required = ["reservas_recepcion", "viajeros_recepcion", "pagos_reserva_recepcion", "recepcion_facturas"]
    for table in required:
        if not table_exists(conn, table):
            add(issues, "recepcion", "critica", f"sin_{table}", f"Falta tabla {table}", "No se puede validar recepcion completa.")
            return

    where = reservas_where(centro_id)
    reservas = int(scalar(conn, f"SELECT COUNT(*) FROM reservas_recepcion WHERE {where}"))
    if reservas == 0:
        add(issues, "recepcion", "alta", "sin_reservas_vida", "No hay reservas activas de vida operativa", "El simulador no puede probar PMS sin reservas.")
        return

    no_travellers = int(
        scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM reservas_recepcion r
            WHERE {where}
              AND COALESCE(r.checkin_at, '') <> ''
              AND NOT EXISTS (SELECT 1 FROM viajeros_recepcion v WHERE v.reserva_id=r.id)
            """,
        )
    )
    if no_travellers:
        add(
            issues,
            "recepcion",
            "alta",
            "checkin_sin_viajeros",
            "Reservas alojadas sin viajeros",
            f"Hay {no_travellers} reservas con check-in pero sin viajeros registrados.",
            {"reservas": no_travellers},
        )

    balance_rows = conn.execute(
        f"""
        SELECT id, codigo, precio_total, deposito, saldo
        FROM reservas_recepcion
        WHERE {where}
          AND ABS(ROUND(COALESCE(precio_total,0) - COALESCE(deposito,0) - COALESCE(saldo,0), 2)) > 0.05
        LIMIT 20
        """
    ).fetchall()
    if balance_rows:
        add(
            issues,
            "recepcion",
            "alta",
            "reserva_balance_no_cuadra",
            "Reservas con precio/deposito/saldo descuadrados",
            "Precio total debe cuadrar con deposito mas saldo.",
            [dict(r) for r in balance_rows],
        )

    factura_rows = conn.execute(
        """
        SELECT id, numero_completo, reserva_id, total, pagado, saldo
        FROM recepcion_facturas
        WHERE empresa_id=1
          AND (serie='SIMH1' OR reserva_id IN (SELECT id FROM reservas_recepcion WHERE codigo LIKE 'VIDA-H1-%'))
          AND ABS(ROUND(COALESCE(total,0) - COALESCE(pagado,0) - COALESCE(saldo,0), 2)) > 0.05
        LIMIT 20
        """
    ).fetchall()
    if factura_rows:
        add(
            issues,
            "recepcion",
            "alta",
            "factura_balance_no_cuadra",
            "Facturas con total/pagado/saldo descuadrados",
            "Total factura debe cuadrar con pagado mas saldo.",
            [dict(r) for r in factura_rows],
        )

    pagos = int(scalar(conn, "SELECT COUNT(*) FROM pagos_reserva_recepcion WHERE referencia LIKE 'PAY-VIDA-H1-%'"))
    facturas = int(scalar(conn, "SELECT COUNT(*) FROM recepcion_facturas WHERE serie='SIMH1'"))
    if pagos < reservas * 0.45:
        add(issues, "recepcion", "media", "pocos_pagos", "Pocos pagos frente a reservas", f"{pagos} pagos para {reservas} reservas simuladas.", {"reservas": reservas, "pagos": pagos})
    if facturas < reservas * 0.30:
        add(issues, "recepcion", "media", "pocas_facturas", "Pocas facturas frente a reservas", f"{facturas} facturas para {reservas} reservas simuladas.", {"reservas": reservas, "facturas": facturas})


def audit_produccion(conn: sqlite3.Connection, corrida_id: int, issues: list[dict]) -> None:
    if not table_exists(conn, "lineas_produccion") or not table_exists(conn, "partes_produccion"):
        add(issues, "produccion", "critica", "sin_partes_produccion", "No hay partes/lineas de produccion", "No se puede validar cocina ni buffet.")
        return
    if not table_exists(conn, "produccion_ingredientes_reales"):
        add(issues, "produccion", "critica", "sin_ingredientes_reales", "Falta trazabilidad de ingredientes reales", "Las lineas no pueden sacar producto real de camara.")
        return

    lineas_sin_ingredientes = conn.execute(
        """
        SELECT lp.id, lp.parte_id, lp.receta_id, p.fecha, p.turno
        FROM lineas_produccion lp
        JOIN partes_produccion p ON p.id=lp.parte_id
        WHERE p.centro_id=3
          AND NOT EXISTS (
            SELECT 1 FROM produccion_ingredientes_reales pir
            WHERE pir.linea_produccion_id=lp.id
          )
        LIMIT 25
        """
    ).fetchall()
    if lineas_sin_ingredientes:
        add(
            issues,
            "produccion",
            "alta",
            "lineas_sin_ingredientes",
            "Lineas de produccion sin ingredientes reales",
            "Hay platos producidos sin consumo real asociado.",
            [dict(r) for r in lineas_sin_ingredientes],
        )

    if table_exists(conn, "produccion_controles_appcc"):
        sin_appcc = conn.execute(
            """
            SELECT lp.id, lp.parte_id, p.fecha, p.turno
            FROM lineas_produccion lp
            JOIN partes_produccion p ON p.id=lp.parte_id
            WHERE p.centro_id=3
              AND NOT EXISTS (
                SELECT 1 FROM produccion_controles_appcc c
                WHERE c.linea_produccion_id=lp.id
              )
            LIMIT 25
            """
        ).fetchall()
        if sin_appcc:
            add(
                issues,
                "appcc",
                "alta",
                "lineas_sin_appcc",
                "Lineas de produccion sin control APPCC",
                "Cada linea operativa debe tener temperatura, responsable y destino de sobrante.",
                [dict(r) for r in sin_appcc],
            )
    else:
        add(issues, "appcc", "critica", "sin_controles_appcc", "Falta tabla de controles APPCC", "No se puede validar seguridad alimentaria de produccion.")

    if table_exists(conn, "simulador_produccion_resumen_diario"):
        dias_sin_camara = conn.execute(
            """
            SELECT fecha, lineas, ingredientes_reales, movimientos_camara, controles_appcc
            FROM simulador_produccion_resumen_diario
            WHERE corrida_id=? AND lineas > 0
              AND (ingredientes_reales <= 0 OR movimientos_camara <= 0 OR controles_appcc <= 0)
            LIMIT 25
            """,
            (corrida_id,),
        ).fetchall()
        if dias_sin_camara:
            add(
                issues,
                "produccion",
                "alta",
                "resumen_dia_incompleto",
                "Dias de produccion sin camara, ingredientes o APPCC",
                "La vida diaria debe arrastrar producto, controles y lectura operativa.",
                [dict(r) for r in dias_sin_camara],
            )
    else:
        add(issues, "produccion", "media", "sin_resumen_produccion", "Falta resumen diario de produccion", "No se puede ver la vida de cocina dia a dia.")


def audit_compras(conn: sqlite3.Connection, issues: list[dict]) -> None:
    if not table_exists(conn, "pedidos") or not table_exists(conn, "recepciones"):
        add(issues, "compras", "critica", "sin_compras_recepcion", "Faltan pedidos o recepciones", "No se puede probar ciclo proveedor-entrada.")
        return

    cerrados_sin_recepcion = conn.execute(
        """
        SELECT p.id, p.numero_pedido, p.estado, p.fecha_pedido, p.fecha_recepcion
        FROM pedidos p
        WHERE p.empresa_id=1
          AND (p.estado IN ('cerrado','recibido','recepcionado') OR COALESCE(p.cerrado,0)=1)
          AND NOT EXISTS (SELECT 1 FROM recepciones r WHERE r.pedido_id=p.id)
        LIMIT 25
        """
    ).fetchall()
    if cerrados_sin_recepcion:
        add(
            issues,
            "compras",
            "alta",
            "pedido_cerrado_sin_recepcion",
            "Pedidos cerrados sin recepcion",
            "Si el pedido esta cerrado debe tener entrada de mercancia o justificante.",
            [dict(r) for r in cerrados_sin_recepcion],
        )

    recepciones_sin_stock = int(
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM recepciones
            WHERE empresa_id=1 AND tipo_recepcion='pedido'
              AND COALESCE(stock_aplicado,0)=0
            """,
        )
    )
    if recepciones_sin_stock:
        add(
            issues,
            "compras",
            "media",
            "recepcion_sin_stock",
            "Recepciones sin aplicar a stock",
            f"Hay {recepciones_sin_stock} recepciones de pedido sin stock aplicado.",
            {"recepciones": recepciones_sin_stock},
        )


def audit_rrhh(conn: sqlite3.Connection, corrida_id: int, issues: list[dict]) -> None:
    if not table_exists(conn, "turnos_rrhh") or not table_exists(conn, "fichajes"):
        add(issues, "rrhh", "critica", "sin_turnos_fichajes", "Faltan turnos o fichajes", "No se puede validar plantilla real.")
        return

    turnos = int(scalar(conn, "SELECT COUNT(*) FROM turnos_rrhh WHERE origen='simulador_vida'"))
    fichajes = int(scalar(conn, "SELECT COUNT(*) FROM fichajes WHERE origen='simulador_vida'"))
    if turnos and fichajes < turnos * 0.30:
        add(issues, "rrhh", "alta", "fichajes_muy_bajos", "Muy pocos fichajes frente a turnos", f"{fichajes} fichajes para {turnos} turnos simulados.", {"turnos": turnos, "fichajes": fichajes})

    if table_exists(conn, "simulador_rrhh_control_diario"):
        criticos = int(
            scalar(
                conn,
                "SELECT COUNT(*) FROM simulador_rrhh_control_diario WHERE corrida_id=? AND nivel='critica'",
                (corrida_id,),
            )
        )
        dias = int(scalar(conn, "SELECT COUNT(*) FROM simulador_rrhh_control_diario WHERE corrida_id=?", (corrida_id,)))
        if dias and criticos > dias * 0.70:
            add(
                issues,
                "rrhh",
                "media",
                "rrhh_demasiado_critico",
                "Demasiados dias criticos de fichajes",
                f"{criticos} dias criticos sobre {dias}. Sirve para estresar, pero no representa una operacion estable.",
                {"dias": dias, "criticos": criticos},
            )
    else:
        add(issues, "rrhh", "media", "sin_control_rrhh_diario", "Falta diagnostico diario de RRHH", "No se esta midiendo absentismo, salidas anticipadas y extras.")


def audit_tpv(conn: sqlite3.Connection, issues: list[dict]) -> None:
    if not all(table_exists(conn, t) for t in ("tpv_cuentas", "tpv_comandas", "tpv_comanda_lineas")):
        add(issues, "tpv", "critica", "sin_tpv_completo", "Faltan tablas TPV", "No se puede validar bar/restaurante.")
        return

    comandas_sin_lineas = conn.execute(
        """
        SELECT c.id, c.numero, c.total, c.estado
        FROM tpv_comandas c
        WHERE c.numero LIKE 'CMD-H1-%'
          AND NOT EXISTS (SELECT 1 FROM tpv_comanda_lineas l WHERE l.comanda_id=c.id)
        LIMIT 25
        """
    ).fetchall()
    if comandas_sin_lineas:
        add(
            issues,
            "tpv",
            "alta",
            "comanda_sin_lineas",
            "Comandas sin lineas",
            "Una comanda simulada debe llevar articulos o receta asociada.",
            [dict(r) for r in comandas_sin_lineas],
        )

    descuadres = conn.execute(
        """
        SELECT c.id, c.numero, c.total, ROUND(COALESCE(SUM(l.total),0), 2) total_lineas
        FROM tpv_comandas c
        LEFT JOIN tpv_comanda_lineas l ON l.comanda_id=c.id
        WHERE c.numero LIKE 'CMD-H1-%'
        GROUP BY c.id
        HAVING ABS(ROUND(COALESCE(c.total,0) - COALESCE(SUM(l.total),0), 2)) > 0.10
        LIMIT 25
        """
    ).fetchall()
    if descuadres:
        add(
            issues,
            "tpv",
            "alta",
            "comanda_total_no_cuadra",
            "Total de comanda no cuadra con lineas",
            "La venta del TPV debe cuadrar con sus lineas.",
            [dict(r) for r in descuadres],
        )

    cuentas = int(scalar(conn, "SELECT COUNT(*) FROM tpv_cuentas WHERE numero LIKE 'CTA-H1-%'"))
    comandas = int(scalar(conn, "SELECT COUNT(*) FROM tpv_comandas WHERE numero LIKE 'CMD-H1-%'"))
    if cuentas and comandas < cuentas * 0.50:
        add(issues, "tpv", "media", "pocas_comandas_por_cuenta", "Pocas comandas frente a cuentas", f"{comandas} comandas para {cuentas} cuentas.", {"cuentas": cuentas, "comandas": comandas})


def audit_mantenimiento(conn: sqlite3.Connection, issues: list[dict]) -> None:
    if not table_exists(conn, "ordenes_trabajo") or not table_exists(conn, "equipos_mantenimiento"):
        add(issues, "mantenimiento", "critica", "sin_mantenimiento", "Faltan equipos u ordenes", "No se puede validar averias, preventivos y activos.")
        return

    sin_equipo = conn.execute(
        """
        SELECT ot.id, ot.numero_orden, ot.equipo_id, ot.estado, ot.descripcion
        FROM ordenes_trabajo ot
        WHERE ot.numero_orden LIKE 'OT-VIDA-H1-%'
          AND COALESCE(ot.equipo_id,0) > 0
          AND NOT EXISTS (SELECT 1 FROM equipos_mantenimiento e WHERE e.id=ot.equipo_id)
        LIMIT 25
        """
    ).fetchall()
    if sin_equipo:
        add(
            issues,
            "mantenimiento",
            "critica",
            "orden_equipo_inexistente",
            "Ordenes apuntan a equipos inexistentes",
            "La trazabilidad de mantenimiento queda rota.",
            [dict(r) for r in sin_equipo],
        )

    cerradas_sin_cierre = conn.execute(
        """
        SELECT id, numero_orden, estado, fecha_fin, fecha_cierre
        FROM ordenes_trabajo
        WHERE numero_orden LIKE 'OT-VIDA-H1-%'
          AND estado IN ('cerrada','cerrado','finalizada','completada')
          AND COALESCE(fecha_cierre, fecha_fin, '') = ''
        LIMIT 25
        """
    ).fetchall()
    if cerradas_sin_cierre:
        add(
            issues,
            "mantenimiento",
            "media",
            "orden_cerrada_sin_fecha",
            "Ordenes cerradas sin fecha de cierre",
            "Las ordenes finalizadas deben registrar fecha de cierre o fin.",
            [dict(r) for r in cerradas_sin_cierre],
        )


def score_from_issues(issues: list[dict]) -> tuple[float, str]:
    weights = {"critica": 16, "alta": 8, "media": 3, "baja": 1}
    penalty = sum(weights.get(i["severidad"], 2) for i in issues)
    score = max(0.0, 100.0 - penalty)
    if score >= 90:
        lectura = "Trazabilidad fuerte: los modulos principales estan conectados."
    elif score >= 75:
        lectura = "Trazabilidad buena, con puntos concretos a reforzar."
    elif score >= 55:
        lectura = "Trazabilidad util para pruebas, pero hay conexiones que revisar."
    else:
        lectura = "Trazabilidad debil: no usar resultados de IA sin corregir enlaces clave."
    return round(score, 1), lectura


def persist(conn: sqlite3.Connection, corrida_id: int, empresa_id: int, centro_id: int, issues: list[dict]) -> None:
    conn.execute("DELETE FROM simulador_trazabilidad_operativa WHERE corrida_id=? AND empresa_id=? AND centro_id=?", (corrida_id, empresa_id, centro_id))
    conn.execute("DELETE FROM simulador_trazabilidad_resumen WHERE corrida_id=? AND empresa_id=? AND centro_id=?", (corrida_id, empresa_id, centro_id))
    for i in issues:
        conn.execute(
            """
            INSERT INTO simulador_trazabilidad_operativa
            (corrida_id, area, severidad, codigo, titulo, detalle, evidencia_json, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                i["area"],
                i["severidad"],
                i["codigo"],
                i["titulo"],
                i["detalle"],
                json.dumps(i["evidencia"], ensure_ascii=False),
                empresa_id,
                centro_id,
            ),
        )
    score, lectura = score_from_issues(issues)
    counts = {sev: sum(1 for i in issues if i["severidad"] == sev) for sev in ("critica", "alta", "media", "baja")}
    conn.execute(
        """
        INSERT INTO simulador_trazabilidad_resumen
        (corrida_id, total, criticas, altas, medias, bajas, score, lectura, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (corrida_id, len(issues), counts["critica"], counts["alta"], counts["media"], counts["baja"], score, lectura, empresa_id, centro_id),
    )
    conn.commit()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida para auditar.")
        issues: list[dict] = []
        audit_recepcion(conn, args.centro_id, issues)
        audit_produccion(conn, corrida_id, issues)
        audit_compras(conn, issues)
        audit_rrhh(conn, corrida_id, issues)
        audit_tpv(conn, issues)
        audit_mantenimiento(conn, issues)
        persist(conn, corrida_id, args.empresa_id, args.centro_id, issues)
        score, lectura = score_from_issues(issues)
        print({"corrida_id": corrida_id, "incidencias": len(issues), "score": score, "lectura": lectura})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

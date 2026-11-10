# -*- coding: utf-8 -*-
"""Refuerza vida comercial y compras de Hotel 1.

No borra datos. Inserta reservas adicionales donde la ocupacion simulada queda
baja y genera pedidos/recepciones diarios por familias para probar compras,
proveedores, stock y economato con mas realismo.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3

TARGETS = {
    1: 0.72,
    2: 0.70,
    3: 0.68,
    4: 0.62,
    5: 0.72,
    6: 0.92,
    7: 0.93,
    8: 0.82,
    9: 0.86,
    10: 0.84,
    11: 0.88,
    12: 0.82,
}

FAMILY_GROUPS = {
    "frescos": ["verdura", "fruta", "hortal", "ensalada", "patata"],
    "carnes": ["carne", "pollo", "cerdo", "ternera", "embutido"],
    "pescados": ["pescado", "marisco", "merluza", "atun", "langost"],
    "lacteos": ["lacteo", "leche", "queso", "huevo", "yogur"],
    "secos": ["seco", "arroz", "pasta", "harina", "aceite", "conserva"],
    "bebidas": ["bebida", "agua", "refresco", "vino", "cerveza", "licor"],
    "limpieza": ["limpieza", "quimico", "detergente", "papel", "amenity"],
}

NOMBRES = [
    "Familia Ferrer", "Grupo Muller", "Ana Martin", "Oliver Smith", "Giulia Rossi",
    "Sophie Laurent", "Carlos Navarro", "Marta Leon", "Luca Costa", "Emma Garcia",
    "Club Senderismo Norte", "Equipo Senior Viajes", "Escapada Familiar",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inyectar compras y ocupacion Hotel 1.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--desde", default="2026-05-30")
    parser.add_argument("--dias", type=int, default=180)
    parser.add_argument("--corrida-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--seed", type=int, default=20260530)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def money(value: float) -> float:
    return round(float(value or 0), 2)


def rand_ref(prefix: str, day: date, serial: int) -> str:
    return f"{prefix}-{day.strftime('%Y%m%d')}-{serial:05d}"


def log_event(conn: sqlite3.Connection, corrida_id: int, day: date, modulo: str, tipo: str, severidad: str, descripcion: str, tabla: str, registro_id: int, data: dict, centro_id: int) -> None:
    if not table_exists(conn, "simulador_vida_eventos"):
        return
    conn.execute(
        """
        INSERT INTO simulador_vida_eventos
        (corrida_id, fecha_simulada, modulo, tipo, severidad, descripcion,
         tabla_ref, registro_id, impacto_json, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (corrida_id, day.isoformat(), modulo, tipo, severidad, descripcion, tabla, registro_id, json.dumps(data, ensure_ascii=False), centro_id),
    )


def habitaciones(conn: sqlite3.Connection, centro_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM habitaciones_recepcion
        WHERE empresa_id=? AND centro_id=? AND COALESCE(activo,1)=1
        ORDER BY numero
        """,
        (EMPRESA_ID, centro_id),
    ).fetchall()


def room_free(conn: sqlite3.Connection, room_id: int, entrada: date, salida: date) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*) total
        FROM reservas_recepcion
        WHERE habitacion_id=?
          AND LOWER(COALESCE(estado,'')) NOT IN ('cancelada','cancelado','no_show','no show')
          AND date(fecha_entrada) < date(?)
          AND date(fecha_salida) > date(?)
        """,
        (room_id, salida.isoformat(), entrada.isoformat()),
    ).fetchone()
    if int(row["total"] or 0) > 0:
        return False
    if table_exists(conn, "bloqueos_habitacion_recepcion"):
        block = conn.execute(
            """
            SELECT COUNT(*) total
            FROM bloqueos_habitacion_recepcion
            WHERE habitacion_id=? AND estado='activo'
              AND date(fecha_inicio) < date(?)
              AND date(fecha_fin) > date(?)
            """,
            (room_id, salida.isoformat(), entrada.isoformat()),
        ).fetchone()
        return int(block["total"] or 0) == 0
    return True


def ocupadas_dia(conn: sqlite3.Connection, day: date, centro_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT habitacion_id) total
        FROM reservas_recepcion
        WHERE empresa_id=? AND centro_id=? AND habitacion_id IS NOT NULL
          AND LOWER(COALESCE(estado,'')) NOT IN ('cancelada','cancelado','no_show','no show')
          AND date(fecha_entrada) <= date(?)
          AND date(fecha_salida) > date(?)
        """,
        (EMPRESA_ID, centro_id, day.isoformat(), day.isoformat()),
    ).fetchone()
    return int(row["total"] or 0)


def create_reserva(conn: sqlite3.Connection, room: sqlite3.Row, entrada: date, salida: date, serial: int, corrida_id: int, centro_id: int) -> int:
    noches = max((salida - entrada).days, 1)
    adultos = random.choice([1, 2, 2, 2, 3])
    menores = random.choice([0, 0, 0, 1, 2]) if adultos >= 2 else 0
    adr = random.uniform(92, 165)
    if entrada.month in {7, 8}:
        adr *= random.uniform(1.05, 1.22)
    if entrada.month in {1, 2, 3, 11}:
        adr *= random.uniform(0.86, 1.02)
    total = money(adr * noches)
    deposito = money(total * random.choice([0.20, 0.30, 0.50, 1.0]))
    codigo = rand_ref("LLENO-H1", entrada, serial)
    estado = "confirmada"
    if entrada <= date(2026, 5, 30) < salida:
        estado = "alojado"
    conn.execute(
        """
        INSERT INTO reservas_recepcion
        (codigo, habitacion_id, fecha_entrada, fecha_salida, hora_llegada, hora_salida,
         regimen, adultos, menores, estado, origen, titular_nombre, titular_email,
         titular_telefono, precio_total, deposito, saldo, observaciones,
         observaciones_operativas, empresa_id, creado_por, fecha_creacion, canal_id,
         localizador_externo, comision_canal, estado_externo, politica_credito,
         tipo_habitacion_id, centro_id)
        VALUES (?, ?, ?, ?, '14:00', '12:00', ?, ?, ?, ?, 'simulador_refuerzo',
                ?, ?, '', ?, ?, ?, 'Reserva de refuerzo para llenar Hotel 1',
                'Generada por simulador compras/ocupacion', 1, 1, CURRENT_TIMESTAMP,
                NULL, ?, ?, 'sincronizada', 'abierto', ?, ?)
        """,
        (
            codigo,
            room["id"],
            entrada.isoformat(),
            salida.isoformat(),
            random.choice(["media_pension", "todo_incluido", "alojamiento_desayuno"]),
            adultos,
            menores,
            estado,
            random.choice(NOMBRES),
            f"cliente{serial}@hotel1.demo",
            total,
            deposito,
            money(total - deposito),
            codigo.replace("LLENO-", "LOC-"),
            money(total * random.choice([0.0, 0.08, 0.12, 0.18])),
            room["tipo_habitacion_id"],
            centro_id,
        ),
    )
    reserva_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    if table_exists(conn, "pagos_reserva_recepcion") and deposito > 0:
        conn.execute(
            """
            INSERT INTO pagos_reserva_recepcion
            (reserva_id, fecha, concepto, importe, forma_pago, referencia, estado,
             observaciones, empresa_id, usuario_id)
            VALUES (?, ?, 'Deposito reserva', ?, ?, ?, 'confirmado',
                    'Pago generado por simulador de ocupacion', 1, 1)
            """,
            (reserva_id, entrada.isoformat(), deposito, random.choice(["tarjeta", "transferencia", "cargo_agencia"]), f"PAY-{codigo}"),
        )
    if table_exists(conn, "recepcion_facturas") and table_exists(conn, "recepcion_factura_lineas") and random.random() < 0.45:
        base = money(total / 1.10)
        impuesto = money(total - base)
        pagado = total if random.random() < 0.70 else deposito
        saldo = money(max(0, total - pagado))
        numero = reserva_id
        conn.execute(
            """
            INSERT INTO recepcion_facturas
            (reserva_id, tipo, serie, ejercicio, numero, numero_completo, fecha,
             cliente_nombre, cliente_email, base, impuestos, total, pagado, saldo,
             estado, observaciones, empresa_id, usuario_id, idioma, impuesto_jurisdiccion)
            VALUES (?, 'factura', 'SIMLLENO', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, 'Factura simulada por refuerzo ocupacion', 1, 1, 'es', 'IVA')
            """,
            (
                reserva_id,
                salida.year,
                numero,
                f"SIMLLENO-{salida.year}-{numero:06d}",
                salida.isoformat(),
                random.choice(NOMBRES),
                f"cliente{serial}@hotel1.demo",
                base,
                impuesto,
                total,
                pagado,
                saldo,
                "cobrada" if saldo <= 0.05 else "pendiente_cobro",
            ),
        )
        factura_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT INTO recepcion_factura_lineas
            (factura_id, origen, origen_id, concepto, cantidad, precio_unitario,
             base, impuesto_pct, impuesto, total, orden, empresa_id, impuesto_codigo,
             impuesto_nombre)
            VALUES (?, 'reserva', ?, ?, 1, ?, ?, 10, ?, ?, 1, 1, 'IVA10', 'IVA reducido')
            """,
            (factura_id, reserva_id, f"Estancia Hotel 1 {entrada.isoformat()} a {salida.isoformat()}", base, base, impuesto, total),
        )
    log_event(conn, corrida_id, entrada, "recepcion", "reserva_refuerzo", "info", f"Reserva de refuerzo {codigo}", "reservas_recepcion", reserva_id, {"habitacion": room["numero"], "total": total}, centro_id)
    return reserva_id


def reforzar_ocupacion(conn: sqlite3.Connection, start: date, end: date, corrida_id: int, centro_id: int) -> int:
    rooms = habitaciones(conn, centro_id)
    if not rooms:
        return 0
    created = 0
    serial = int(conn.execute("SELECT COUNT(*) FROM reservas_recepcion WHERE codigo LIKE 'LLENO-H1-%'").fetchone()[0] or 0) + 1
    day = start
    while day <= end:
        target = int(len(rooms) * TARGETS.get(day.month, 0.76))
        current = ocupadas_dia(conn, day, centro_id)
        deficit = max(0, target - current)
        attempts = 0
        while deficit > 0 and attempts < len(rooms) * 2:
            attempts += 1
            stay = random.randint(2, 6)
            entrada = day + timedelta(days=random.randint(0, 2))
            salida = min(end + timedelta(days=1), entrada + timedelta(days=stay))
            if salida <= entrada:
                break
            room = random.choice(rooms)
            if not room_free(conn, int(room["id"]), entrada, salida):
                continue
            create_reserva(conn, room, entrada, salida, serial, corrida_id, centro_id)
            serial += 1
            created += 1
            deficit -= stay
        day += timedelta(days=1)
    return created


def product_pool(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    rows = conn.execute(
        """
        SELECT *
        FROM productos
        WHERE empresa_id=? AND COALESCE(activo,1)=1 AND COALESCE(visible_en_compras,1)=1
          AND COALESCE(permite_compra_directa,1)=1
        """,
        (EMPRESA_ID,),
    ).fetchall()
    pool = {key: [] for key in FAMILY_GROUPS}
    pool["otros"] = []
    for row in rows:
        text = f"{row['nombre']} {row['familia']} {row['tipo_producto']}".lower()
        placed = False
        for group, keys in FAMILY_GROUPS.items():
            if any(k in text for k in keys):
                pool[group].append(row)
                placed = True
                break
        if not placed:
            pool["otros"].append(row)
    return pool


def department_id(conn: sqlite3.Connection, centro_id: int, name_like: str) -> int | None:
    row = conn.execute(
        """
        SELECT id FROM departamentos
        WHERE empresa_id=? AND centro_id=? AND UPPER(nombre) LIKE UPPER(?)
        ORDER BY id LIMIT 1
        """,
        (EMPRESA_ID, centro_id, f"%{name_like}%"),
    ).fetchone()
    return int(row["id"]) if row else None


def proveedor_for_products(products: list[sqlite3.Row]) -> int | None:
    ids = [int(p["proveedor_id"]) for p in products if p["proveedor_id"]]
    return random.choice(ids) if ids else None


def fallback_provider(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT id FROM proveedores WHERE empresa_id=? AND COALESCE(activo,1)=1 ORDER BY RANDOM() LIMIT 1", (EMPRESA_ID,)).fetchone()
    return int(row["id"]) if row else None


def create_order(conn: sqlite3.Connection, day: date, group: str, products: list[sqlite3.Row], serial: int, corrida_id: int, centro_id: int) -> int | None:
    if not products:
        return None
    sample = random.sample(products, k=min(len(products), random.randint(4, 10)))
    provider_id = proveedor_for_products(sample) or fallback_provider(conn)
    if not provider_id:
        return None
    dept = department_id(conn, centro_id, "COCINA") or department_id(conn, centro_id, "BARES") or department_id(conn, centro_id, "PISOS")
    if group in {"bebidas"}:
        dept = department_id(conn, centro_id, "BARES") or dept
    if group in {"limpieza"}:
        dept = department_id(conn, centro_id, "PISOS") or dept
    estado = random.choices(["enviado", "recibido", "cerrado", "parcial"], weights=[18, 42, 26, 14], k=1)[0]
    entrega = day + timedelta(days=random.randint(1, 3))
    numero = rand_ref(f"COMPRA-H1-{group.upper()}", day, serial)
    conn.execute(
        """
        INSERT INTO pedidos
        (numero_pedido, proveedor_id, fecha_pedido, fecha_entrega, estado,
         observaciones, creado_por, empresa_id, fecha_recepcion, estado_recepcion,
         cerrado, fecha_cierre, departamento_id, departamento_solicitante_id,
         solicitado_por, tipo_pedido, origen_documento, validado_economicamente)
        VALUES (?, ?, ?, ?, ?, ?, 'Simulador compras vivo', 1, ?, ?, ?, ?, ?, ?,
                'Simulador compras vivo', ?, 'simulador_compras_ocupacion', ?)
        """,
        (
            numero,
            provider_id,
            day.isoformat(),
            entrega.isoformat(),
            estado,
            f"Pedido {group} generado por ocupacion y stock Hotel 1",
            entrega.isoformat() if estado in {"recibido", "cerrado", "parcial"} else None,
            "parcial" if estado == "parcial" else "recibido" if estado in {"recibido", "cerrado"} else "pendiente",
            1 if estado == "cerrado" else 0,
            entrega.isoformat() if estado == "cerrado" else None,
            dept,
            dept,
            group,
            1 if random.random() < 0.35 else 0,
        ),
    )
    pedido_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    total_lineas = 0
    fallo = estado == "parcial" or random.random() < 0.08
    for p in sample:
        base_qty = random.choice([6, 8, 12, 16, 24, 36, 48])
        qty = float(base_qty)
        received = qty if not fallo or random.random() > 0.45 else round(qty * random.choice([0.0, 0.5, 0.75]), 2)
        price = float(p["precio"] or random.uniform(1.2, 18))
        conn.execute(
            """
            INSERT INTO lineas_pedido
            (pedido_id, producto_id, cantidad_solicitada, cantidad_recibida, precio_unitario)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pedido_id, p["id"], qty, received, money(price)),
        )
        total_lineas += 1
        if received > 0:
            conn.execute("UPDATE productos SET cantidad=COALESCE(cantidad,0)+? WHERE id=?", (received, p["id"]))
    if table_exists(conn, "recepciones") and estado in {"recibido", "cerrado", "parcial"}:
        conn.execute(
            """
            INSERT INTO recepciones
            (pedido_id, proveedor_id, fecha_recepcion, numero_albaran, observaciones,
             recibido_por, empresa_id, tipo_recepcion, departamento_receptor_id,
             validada_operativamente, validada_economicamente, stock_aplicado,
             departamento_id, estado_facturacion)
            VALUES (?, ?, ?, ?, ?, 'Economato Hotel 1', 1, 'pedido', ?, ?, ?, 1, ?, 'pendiente_factura')
            """,
            (
                pedido_id,
                provider_id,
                entrega.isoformat(),
                f"ALB-{numero}",
                "Recepcion parcial con faltas" if fallo else "Recepcion correcta",
                dept,
                0 if fallo else 1,
                0,
                dept,
            ),
        )
    severity = "alta" if fallo else "info"
    log_event(conn, corrida_id, day, "compras", "pedido_compra_vivo", severity, f"Pedido vivo {group}: {total_lineas} lineas", "pedidos", pedido_id, {"estado": estado, "fallo": fallo}, centro_id)
    return pedido_id


def reforzar_compras(conn: sqlite3.Connection, start: date, end: date, corrida_id: int, centro_id: int) -> int:
    pool = product_pool(conn)
    created = 0
    serial = int(conn.execute("SELECT COUNT(*) FROM pedidos WHERE numero_pedido LIKE 'COMPRA-H1-%'").fetchone()[0] or 0) + 1
    day = start
    while day <= end:
        groups = ["frescos", "carnes", "pescados", "secos"]
        if day.weekday() in {0, 3, 5}:
            groups.append("bebidas")
        if day.weekday() in {1, 4}:
            groups.append("limpieza")
        if day.day in {5, 15, 25}:
            groups.append("lacteos")
        random.shuffle(groups)
        orders_today = random.randint(1, 3)
        for group in groups[:orders_today]:
            products = pool.get(group) or pool.get("otros") or []
            pedido_id = create_order(conn, day, group, products, serial, corrida_id, centro_id)
            if pedido_id:
                created += 1
                serial += 1
        day += timedelta(days=1)
    return created


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    start = date.fromisoformat(args.desde)
    end = start + timedelta(days=max(args.dias, 1) - 1)
    conn = connect(db_path)
    try:
        reservas = reforzar_ocupacion(conn, start, end, args.corrida_id, args.centro_id)
        pedidos = reforzar_compras(conn, start, end, args.corrida_id, args.centro_id)
        conn.commit()
        print({
            "centro_id": args.centro_id,
            "desde": start.isoformat(),
            "hasta": end.isoformat(),
            "reservas_creadas": reservas,
            "pedidos_creados": pedidos,
        })
    finally:
        conn.close()


if __name__ == "__main__":
    main()

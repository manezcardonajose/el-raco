"""Instala el modulo reusable de pagos marca blanca en una base SQLite.

Uso:
  python scripts/instalar_modulo_pagos.py --db pepe4.db --demo

El script no toca tarjetas ni claves reales. Solo crea tablas, permisos y una
pasarela simulada opcional para pruebas.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integraciones.pagos_marca_blanca.schema import ensure_payment_schema
from integraciones.pagos_marca_blanca.service import PaymentService


PERMISOS = [
    ("pagos_ver", "Ver pasarelas y operaciones de cobro"),
    ("pagos_configurar", "Configurar pasarelas de pago y secretos tecnicos"),
    ("pagos_operar", "Crear operaciones, links, preautorizaciones y devoluciones"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=None)
    parser.add_argument("--demo", action="store_true")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def seed_permissions(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "permisos"):
        return 0
    total = 0
    for nombre, descripcion in PERMISOS:
        cur = conn.execute("INSERT OR IGNORE INTO permisos (nombre, descripcion) VALUES (?, ?)", (nombre, descripcion))
        total += cur.rowcount if cur.rowcount is not None else 0
    return total


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    if not db.is_absolute():
        db = ROOT / db
    conn = connect(db)
    try:
        ensure_payment_schema(conn)
        permisos = seed_permissions(conn)
        service = PaymentService(conn, empresa_id=args.empresa_id, centro_id=args.centro_id, usuario="instalador")
        plantillas = service.seed_default_templates()
        demo_id = None
        if args.demo:
            actual = conn.execute(
                """
                SELECT id FROM pagos_gateway_config
                WHERE empresa_id=? AND proveedor='simulado' AND nombre='Pasarela demo'
                LIMIT 1
                """,
                (args.empresa_id,),
            ).fetchone()
            if actual:
                demo_id = int(actual["id"])
            else:
                demo_id = service.save_config(
                    nombre="Pasarela demo",
                    proveedor="simulado",
                    modo="test",
                    activo=1,
                    marca_nombre="El Raco demo",
                    marca_color="#ff6b00",
                    moneda="EUR",
                    metodos={"tarjeta": True, "bizum": True, "link": True, "nfc": True},
                    config_publica={"success_url": "/pagos/ok", "cancel_url": "/pagos/ko"},
                    secretos={"webhook_secret": "env:PAGOS_SIM_WEBHOOK_SECRET"},
                )
            terminal = conn.execute(
                """
                SELECT id FROM pagos_terminales
                WHERE empresa_id=? AND config_id=? AND nombre='Terminal demo recepcion'
                LIMIT 1
                """,
                (args.empresa_id, demo_id),
            ).fetchone()
            if not terminal:
                service.save_terminal(
                    config_id=demo_id,
                    nombre="Terminal demo recepcion",
                    proveedor_terminal_id="SIM-TERMINAL-RECEPCION",
                    ubicacion="Recepcion",
                    nfc=1,
                    tap_to_pay=1,
                    activo=1,
                )
        conn.commit()
        print(json.dumps({"ok": True, "db": str(db), "permisos_creados": permisos, "plantillas_creadas": plantillas, "demo_config_id": demo_id}, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

"""Schema SQLite del modulo reusable de pagos."""

from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.strip().split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def ensure_payment_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pagos_gateway_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            nombre TEXT NOT NULL,
            proveedor TEXT NOT NULL,
            modo TEXT DEFAULT 'test',
            activo INTEGER DEFAULT 1,
            marca_nombre TEXT,
            marca_logo TEXT,
            marca_color TEXT,
            moneda TEXT DEFAULT 'EUR',
            metodos_json TEXT DEFAULT '{}',
            config_publica_json TEXT DEFAULT '{}',
            secretos_json TEXT DEFAULT '{}',
            terminales_json TEXT DEFAULT '{}',
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pagos_terminales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_id INTEGER NOT NULL,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            nombre TEXT NOT NULL,
            proveedor_terminal_id TEXT,
            ubicacion TEXT,
            nfc INTEGER DEFAULT 1,
            tap_to_pay INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            ultimo_estado TEXT,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (config_id) REFERENCES pagos_gateway_config(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pagos_operaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            config_id INTEGER,
            referencia_interna TEXT UNIQUE,
            origen_modulo TEXT,
            origen_tipo TEXT,
            origen_id INTEGER,
            tipo_operacion TEXT DEFAULT 'payment',
            importe REAL NOT NULL,
            moneda TEXT DEFAULT 'EUR',
            descripcion TEXT,
            cliente_nombre TEXT,
            cliente_email TEXT,
            metodo TEXT,
            estado TEXT DEFAULT 'creado',
            proveedor TEXT,
            proveedor_payment_id TEXT,
            proveedor_session_id TEXT,
            checkout_url TEXT,
            terminal_id INTEGER,
            parent_operacion_id INTEGER,
            raw_request_json TEXT DEFAULT '{}',
            raw_response_json TEXT DEFAULT '{}',
            error TEXT,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_expiracion DATETIME,
            fecha_confirmacion DATETIME,
            usuario TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pagos_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            config_id INTEGER,
            operacion_id INTEGER,
            proveedor TEXT,
            evento_tipo TEXT,
            evento_id TEXT,
            firma_valida INTEGER DEFAULT 0,
            payload_json TEXT DEFAULT '{}',
            estado_procesado TEXT DEFAULT 'pendiente',
            error TEXT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pagos_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            usuario TEXT,
            accion TEXT NOT NULL,
            resultado TEXT,
            entidad TEXT,
            entidad_id INTEGER,
            detalle_json TEXT DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pagos_comunicaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            operacion_id INTEGER NOT NULL,
            canal TEXT DEFAULT 'email',
            destinatario TEXT,
            asunto TEXT,
            cuerpo TEXT,
            resultado TEXT,
            error TEXT,
            usuario TEXT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (operacion_id) REFERENCES pagos_operaciones(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pagos_plantillas_comunicacion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            nombre TEXT NOT NULL,
            canal TEXT DEFAULT 'email',
            tipo_operacion TEXT DEFAULT 'payment',
            idioma TEXT DEFAULT 'es',
            asunto TEXT,
            cuerpo_html TEXT,
            cuerpo_texto TEXT,
            activo INTEGER DEFAULT 1,
            por_defecto INTEGER DEFAULT 0,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for table in (
        "pagos_gateway_config",
        "pagos_terminales",
        "pagos_operaciones",
        "pagos_eventos",
        "pagos_auditoria",
        "pagos_comunicaciones",
        "pagos_plantillas_comunicacion",
    ):
        _add_column(conn, table, "empresa_id INTEGER DEFAULT 1")
        _add_column(conn, table, "centro_id INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pagos_operaciones_ref ON pagos_operaciones(referencia_interna)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pagos_operaciones_origen ON pagos_operaciones(origen_modulo, origen_tipo, origen_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pagos_eventos_operacion ON pagos_eventos(operacion_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pagos_plantillas_default ON pagos_plantillas_comunicacion(empresa_id, centro_id, canal, tipo_operacion, idioma, activo, por_defecto)")

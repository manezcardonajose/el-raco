"""Senales operativas para el cerebro IA.

Modulo pequeño y reutilizable para que cualquier parte del Raco pueda avisar
al cerebro operativo sin depender de rutas Flask concretas.
"""

import json


def ensure_senales_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_operativo_senales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            modulo TEXT DEFAULT 'Operacion',
            origen TEXT DEFAULT 'manual',
            titulo TEXT NOT NULL,
            detalle TEXT,
            severidad TEXT DEFAULT 'media',
            estado TEXT DEFAULT 'pendiente',
            payload_json TEXT,
            usuario TEXT,
            procesado_en DATETIME,
            tarea_id INTEGER,
            recomendacion_id INTEGER
        )
        """
    )


def emitir_senal_operativa(
    conn,
    empresa_id,
    centro_id,
    modulo,
    titulo,
    detalle="",
    severidad="media",
    origen="modulo",
    payload=None,
    usuario="sistema",
    deduplicar=True,
):
    """Crea una señal operativa pendiente.

    Devuelve (creada, id). Si deduplicar=True y ya existe una señal pendiente
    con el mismo titulo para empresa/centro, devuelve (False, id_existente).
    """
    ensure_senales_schema(conn)
    titulo = (titulo or "").strip()
    if not titulo:
        return False, None
    severidad = (severidad or "media").strip().lower()
    if severidad not in {"baja", "media", "alta", "critica"}:
        severidad = "media"
    if deduplicar:
        row = conn.execute(
            """
            SELECT id FROM asistente_operativo_senales
            WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
              AND titulo = ? AND COALESCE(estado,'pendiente') = 'pendiente'
            LIMIT 1
            """,
            (empresa_id, centro_id, titulo),
        ).fetchone()
        if row:
            try:
                return False, row["id"]
            except Exception:
                return False, row[0]
    cur = conn.execute(
        """
        INSERT INTO asistente_operativo_senales (
            empresa_id, centro_id, modulo, origen, titulo, detalle,
            severidad, estado, payload_json, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pendiente', ?, ?)
        """,
        (
            empresa_id,
            centro_id,
            modulo or "Operacion",
            origen or "modulo",
            titulo,
            detalle or "",
            severidad,
            json.dumps(payload or {}, ensure_ascii=False),
            usuario or "sistema",
        ),
    )
    return True, cur.lastrowid


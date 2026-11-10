import unicodedata


def table_exists(conn, table_name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def column_exists(conn, table_name, column_name):
    try:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return False
    return any((col["name"] if hasattr(col, "keys") and "name" in col.keys() else col[1]) == column_name for col in cols)


def add_column_if_missing(conn, table_name, column_sql):
    column_name = column_sql.strip().split()[0]
    if table_exists(conn, table_name) and not column_exists(conn, table_name, column_name):
        try:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
        except Exception as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


def _text_key(value):
    texto = str(value or "").strip().lower()
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


def _cold_storage_config(nombre, tipo):
    texto = _text_key(f"{nombre or ''} {tipo or ''}")
    if any(token in texto for token in ("congel", "freezer")):
        return True, "camara_congelacion", -24, -18
    if any(token in texto for token in ("refriger", "frio", "nevera", "camara", "botellero", "vitrina")):
        return True, "camara_refrigeracion", 0, 5
    return False, "almacen", None, None


def _safe_value(row, key, default=None):
    try:
        if hasattr(row, "keys") and key in row.keys():
            return row[key]
    except Exception:
        pass
    return default


def _valid_fk(conn, table_name, value, empresa_id=None):
    if value in (None, "", 0, "0"):
        return None
    try:
        value = int(value)
    except Exception:
        return None
    if not table_exists(conn, table_name):
        return None
    sql = f"SELECT id FROM {table_name} WHERE id = ?"
    params = [value]
    if empresa_id is not None and column_exists(conn, table_name, "empresa_id"):
        sql += " AND (empresa_id = ? OR empresa_id IS NULL)"
        params.append(empresa_id)
    sql += " LIMIT 1"
    return value if conn.execute(sql, params).fetchone() else None


def _codigo_unificado(prefix, source_id):
    return f"{prefix}-{int(source_id):04d}"


def ensure_equipos_unificados_schema(conn):
    """Une inventario, APPCC y almacenes sin romper las tablas operativas actuales."""
    if not (table_exists(conn, "equipos_mantenimiento") and table_exists(conn, "equipos")):
        return

    for col in [
        "codigo TEXT",
        "familia TEXT",
        "categoria_operativa TEXT",
        "clase_activo TEXT",
        "criticidad TEXT DEFAULT 'media'",
        "inventariable INTEGER DEFAULT 1",
        "requiere_appcc INTEGER DEFAULT 0",
        "equipo_appcc_id INTEGER",
        "origen_modulo TEXT",
        "habitacion_id INTEGER",
        "zona_id INTEGER",
        "almacen_id INTEGER",
        "centro_id INTEGER",
    ]:
        add_column_if_missing(conn, "equipos_mantenimiento", col)

    for col in [
        "departamento_id INTEGER",
        "subdepartamento_id INTEGER",
        "punto_venta_id INTEGER",
        "codigo TEXT",
        "equipo_mantenimiento_id INTEGER",
        "almacen_id INTEGER",
        "centro_id INTEGER",
        "requiere_mantenimiento INTEGER DEFAULT 1",
    ]:
        add_column_if_missing(conn, "equipos", col)

    if table_exists(conn, "almacenes"):
        for col in [
            "centro_id INTEGER",
            "departamento_id INTEGER",
            "subdepartamento_id INTEGER",
            "equipo_mantenimiento_id INTEGER",
            "equipo_appcc_id INTEGER",
            "requiere_control_temperatura INTEGER DEFAULT 0",
            "rango_min REAL",
            "rango_max REAL",
        ]:
            add_column_if_missing(conn, "almacenes", col)

    # Las bases antiguas no siempre tenian centro en toda la estructura.
    # La unificacion debe migrarlas antes de consultar esas columnas.
    if table_exists(conn, "departamentos"):
        add_column_if_missing(conn, "departamentos", "centro_id INTEGER")
    if table_exists(conn, "puntos_venta"):
        add_column_if_missing(conn, "puntos_venta", "centro_id INTEGER")

    if table_exists(conn, "registros_temperatura"):
        for col in [
            "equipo_mantenimiento_id INTEGER",
            "almacen_id INTEGER",
        ]:
            add_column_if_missing(conn, "registros_temperatura", col)
        conn.execute(
            """
            UPDATE registros_temperatura
            SET equipo_mantenimiento_id = COALESCE(equipo_mantenimiento_id, (
                    SELECT equipo_mantenimiento_id FROM equipos e WHERE e.id = registros_temperatura.equipo_id
                )),
                almacen_id = COALESCE(almacen_id, (
                    SELECT almacen_id FROM equipos e WHERE e.id = registros_temperatura.equipo_id
                ))
            WHERE equipo_id IS NOT NULL
            """
        )

    conn.execute(
        """
        UPDATE equipos
        SET centro_id = COALESCE(
            centro_id,
            (SELECT em.centro_id FROM equipos_mantenimiento em WHERE em.id = equipos.equipo_mantenimiento_id),
            (SELECT a.centro_id FROM almacenes a WHERE a.id = equipos.almacen_id),
            (SELECT d.centro_id FROM departamentos d WHERE d.id = equipos.departamento_id),
            (SELECT pv.centro_id FROM puntos_venta pv WHERE pv.id = equipos.punto_venta_id)
        )
        WHERE centro_id IS NULL
        """
    )

    appcc_rows = conn.execute("""
        SELECT *
        FROM equipos
        WHERE COALESCE(activo, 1) = 1
        ORDER BY id
    """).fetchall()

    for equipo_appcc in appcc_rows:
        empresa_id = _safe_value(equipo_appcc, "empresa_id", 1) or 1
        mantenimiento_id = _safe_value(equipo_appcc, "equipo_mantenimiento_id")
        if mantenimiento_id:
            exists = conn.execute(
                "SELECT id FROM equipos_mantenimiento WHERE id = ? LIMIT 1",
                (mantenimiento_id,),
            ).fetchone()
            if not exists:
                mantenimiento_id = None

        if not mantenimiento_id:
            mantenimiento = conn.execute(
                """
                SELECT id
                FROM equipos_mantenimiento
                WHERE empresa_id = ?
                  AND (
                        equipo_appcc_id = ?
                        OR (
                            LOWER(COALESCE(nombre, '')) = ?
                            AND LOWER(COALESCE(ubicacion, '')) = ?
                        )
                  )
                ORDER BY id
                LIMIT 1
                """,
                (
                    empresa_id,
                    equipo_appcc["id"],
                    _text_key(equipo_appcc["nombre"]),
                    _text_key(_safe_value(equipo_appcc, "ubicacion")),
                ),
            ).fetchone()
            mantenimiento_id = mantenimiento["id"] if mantenimiento else None

        if not mantenimiento_id:
            departamento_id = _valid_fk(conn, "departamentos", _safe_value(equipo_appcc, "departamento_id"), empresa_id)
            subdepartamento_id = _valid_fk(conn, "subdepartamentos", _safe_value(equipo_appcc, "subdepartamento_id"), empresa_id)
            punto_venta_id = _valid_fk(conn, "puntos_venta", _safe_value(equipo_appcc, "punto_venta_id"), empresa_id)
            conn.execute(
                """
                INSERT INTO equipos_mantenimiento (
                    codigo, nombre, tipo, familia, categoria_operativa, clase_activo,
                    criticidad, inventariable, requiere_appcc, equipo_appcc_id,
                    origen_modulo, ubicacion, departamento_id, subdepartamento_id,
                    punto_venta_id, activo, empresa_id, centro_id
                )
                VALUES (?, ?, ?, 'appcc', 'appcc', 'equipo_control',
                        'alta', 1, 1, ?, 'appcc', ?, ?, ?, ?, 1, ?, (
                            SELECT COALESCE(d.centro_id, pv.centro_id)
                            FROM departamentos d
                            LEFT JOIN puntos_venta pv ON pv.id = ?
                            WHERE d.id = ?
                        ))
                """,
                (
                    _codigo_unificado("APPCC", equipo_appcc["id"]),
                    equipo_appcc["nombre"],
                    _safe_value(equipo_appcc, "tipo") or "appcc",
                    equipo_appcc["id"],
                    _safe_value(equipo_appcc, "ubicacion"),
                    departamento_id,
                    subdepartamento_id,
                    punto_venta_id,
                    empresa_id,
                    punto_venta_id,
                    departamento_id,
                ),
            )
            mantenimiento_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        conn.execute(
            """
            UPDATE equipos
            SET equipo_mantenimiento_id = ?,
                requiere_mantenimiento = COALESCE(requiere_mantenimiento, 1),
                codigo = COALESCE(NULLIF(codigo, ''), ?),
                centro_id = COALESCE(
                    centro_id,
                    (SELECT centro_id FROM equipos_mantenimiento WHERE id = ?),
                    (SELECT centro_id FROM almacenes WHERE id = equipos.almacen_id),
                    (SELECT centro_id FROM departamentos WHERE id = equipos.departamento_id),
                    (SELECT centro_id FROM puntos_venta WHERE id = equipos.punto_venta_id)
                )
            WHERE id = ?
            """,
            (mantenimiento_id, _codigo_unificado("APPCC", equipo_appcc["id"]), mantenimiento_id, equipo_appcc["id"]),
        )
        conn.execute(
            """
            UPDATE equipos_mantenimiento
            SET equipo_appcc_id = COALESCE(equipo_appcc_id, ?),
                requiere_appcc = 1,
                ubicacion = COALESCE(NULLIF(ubicacion, ''), ?),
                departamento_id = COALESCE(departamento_id, ?),
                subdepartamento_id = COALESCE(subdepartamento_id, ?),
                punto_venta_id = COALESCE(punto_venta_id, ?)
            WHERE id = ?
            """,
            (
                equipo_appcc["id"],
                _safe_value(equipo_appcc, "ubicacion"),
                _valid_fk(conn, "departamentos", _safe_value(equipo_appcc, "departamento_id"), empresa_id),
                _valid_fk(conn, "subdepartamentos", _safe_value(equipo_appcc, "subdepartamento_id"), empresa_id),
                _valid_fk(conn, "puntos_venta", _safe_value(equipo_appcc, "punto_venta_id"), empresa_id),
                mantenimiento_id,
            ),
        )

    pendientes_appcc = conn.execute("""
        SELECT *
        FROM equipos_mantenimiento
        WHERE COALESCE(activo, 1) = 1
          AND COALESCE(requiere_appcc, 0) = 1
          AND equipo_appcc_id IS NULL
        ORDER BY id
    """).fetchall()
    for equipo in pendientes_appcc:
        empresa_id = _safe_value(equipo, "empresa_id", 1) or 1
        existing = conn.execute(
            """
            SELECT id
            FROM equipos
            WHERE empresa_id = ?
              AND (
                    equipo_mantenimiento_id = ?
                    OR LOWER(COALESCE(nombre, '')) = ?
              )
            ORDER BY id
            LIMIT 1
            """,
            (empresa_id, equipo["id"], _text_key(equipo["nombre"])),
        ).fetchone()
        appcc_id = existing["id"] if existing else None
        if not appcc_id:
            es_frio, tipo_control, rango_min, rango_max = _cold_storage_config(equipo["nombre"], equipo["tipo"])
            departamento_id = _valid_fk(conn, "departamentos", _safe_value(equipo, "departamento_id"), empresa_id)
            subdepartamento_id = _valid_fk(conn, "subdepartamentos", _safe_value(equipo, "subdepartamento_id"), empresa_id)
            punto_venta_id = _valid_fk(conn, "puntos_venta", _safe_value(equipo, "punto_venta_id"), empresa_id)
            almacen_id = _valid_fk(conn, "almacenes", _safe_value(equipo, "almacen_id"), empresa_id)
            conn.execute(
                """
                INSERT INTO equipos (
                    nombre, tipo, ubicacion, departamento_id, subdepartamento_id,
                    punto_venta_id, rango_min, rango_max, responsable, activo,
                    empresa_id, codigo, equipo_mantenimiento_id, almacen_id,
                    centro_id, requiere_mantenimiento
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 1, ?, ?, ?, ?, ?, 1)
                """,
                (
                    equipo["nombre"],
                    tipo_control if es_frio else (equipo["tipo"] or "appcc"),
                    _safe_value(equipo, "ubicacion"),
                    departamento_id,
                    subdepartamento_id,
                    punto_venta_id,
                    rango_min,
                    rango_max,
                    empresa_id,
                    _codigo_unificado("EQ", equipo["id"]),
                    equipo["id"],
                    almacen_id,
                    _safe_value(equipo, "centro_id"),
                ),
            )
            appcc_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            "UPDATE equipos_mantenimiento SET equipo_appcc_id = ? WHERE id = ?",
            (appcc_id, equipo["id"]),
        )
        conn.execute(
            """
            UPDATE equipos
            SET equipo_mantenimiento_id = ?,
                almacen_id = COALESCE(almacen_id, ?),
                centro_id = COALESCE(centro_id, (SELECT centro_id FROM equipos_mantenimiento WHERE id = ?))
            WHERE id = ?
            """,
            (equipo["id"], _valid_fk(conn, "almacenes", _safe_value(equipo, "almacen_id"), empresa_id), equipo["id"], appcc_id),
        )

    if not table_exists(conn, "almacenes"):
        return

    almacenes = conn.execute("""
        SELECT *
        FROM almacenes
        WHERE COALESCE(activo, 1) = 1
        ORDER BY id
    """).fetchall()

    for almacen in almacenes:
        es_frio, tipo_control, rango_min_default, rango_max_default = _cold_storage_config(almacen["nombre"], almacen["tipo"])
        if not es_frio:
            continue

        empresa_id = _safe_value(almacen, "empresa_id", 1) or 1
        rango_min = _safe_value(almacen, "rango_min")
        rango_max = _safe_value(almacen, "rango_max")
        if rango_min is None:
            rango_min = rango_min_default
        if rango_max is None:
            rango_max = rango_max_default

        mantenimiento_id = _safe_value(almacen, "equipo_mantenimiento_id")
        if not mantenimiento_id:
            mantenimiento = conn.execute(
                """
                SELECT id
                FROM equipos_mantenimiento
                WHERE empresa_id = ?
                  AND (
                        almacen_id = ?
                        OR LOWER(COALESCE(nombre, '')) = ?
                  )
                ORDER BY id
                LIMIT 1
                """,
                (empresa_id, almacen["id"], _text_key(f"Camara {almacen['nombre']}")),
            ).fetchone()
            mantenimiento_id = mantenimiento["id"] if mantenimiento else None

        if not mantenimiento_id:
            departamento_id = _valid_fk(conn, "departamentos", _safe_value(almacen, "departamento_id"), empresa_id)
            subdepartamento_id = _valid_fk(conn, "subdepartamentos", _safe_value(almacen, "subdepartamento_id"), empresa_id)
            conn.execute(
                """
                INSERT INTO equipos_mantenimiento (
                    codigo, nombre, tipo, familia, categoria_operativa, clase_activo,
                    criticidad, inventariable, requiere_appcc, origen_modulo,
                    almacen_id, ubicacion, departamento_id, subdepartamento_id,
                    activo, empresa_id, centro_id
                )
                VALUES (?, ?, ?, 'almacenes', 'almacen', 'almacen_frio',
                        'alta', 1, 1, 'almacenes', ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    _codigo_unificado("ALM", almacen["id"]),
                    f"Camara {almacen['nombre']}",
                    tipo_control,
                    almacen["id"],
                    _safe_value(almacen, "ubicacion"),
                    departamento_id,
                    subdepartamento_id,
                    empresa_id,
                    _safe_value(almacen, "centro_id"),
                ),
            )
            mantenimiento_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        appcc_id = _safe_value(almacen, "equipo_appcc_id")
        if not appcc_id:
            appcc = conn.execute(
                """
                SELECT id
                FROM equipos
                WHERE empresa_id = ?
                  AND (
                        almacen_id = ?
                        OR equipo_mantenimiento_id = ?
                        OR LOWER(COALESCE(nombre, '')) = ?
                  )
                ORDER BY id
                LIMIT 1
                """,
                (empresa_id, almacen["id"], mantenimiento_id, _text_key(f"Camara {almacen['nombre']}")),
            ).fetchone()
            appcc_id = appcc["id"] if appcc else None

        if not appcc_id:
            departamento_id = _valid_fk(conn, "departamentos", _safe_value(almacen, "departamento_id"), empresa_id)
            subdepartamento_id = _valid_fk(conn, "subdepartamentos", _safe_value(almacen, "subdepartamento_id"), empresa_id)
            conn.execute(
                """
                INSERT INTO equipos (
                    nombre, tipo, ubicacion, departamento_id, subdepartamento_id,
                    rango_min, rango_max, responsable, activo, empresa_id,
                    codigo, equipo_mantenimiento_id, almacen_id, centro_id, requiere_mantenimiento
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 1)
                """,
                (
                    f"Camara {almacen['nombre']}",
                    tipo_control,
                    _safe_value(almacen, "ubicacion"),
                    departamento_id,
                    subdepartamento_id,
                    rango_min,
                    rango_max,
                    _safe_value(almacen, "responsable") or "",
                    empresa_id,
                    _codigo_unificado("ALM", almacen["id"]),
                    mantenimiento_id,
                    almacen["id"],
                    _safe_value(almacen, "centro_id"),
                ),
            )
            appcc_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        conn.execute(
            """
            UPDATE almacenes
            SET equipo_mantenimiento_id = ?,
                equipo_appcc_id = ?,
                requiere_control_temperatura = 1,
                rango_min = COALESCE(rango_min, ?),
                rango_max = COALESCE(rango_max, ?)
            WHERE id = ?
            """,
            (mantenimiento_id, appcc_id, rango_min, rango_max, almacen["id"]),
        )
        conn.execute(
            """
            UPDATE equipos_mantenimiento
            SET almacen_id = ?,
                equipo_appcc_id = ?,
                requiere_appcc = 1,
                departamento_id = COALESCE(departamento_id, ?),
                subdepartamento_id = COALESCE(subdepartamento_id, ?),
                ubicacion = COALESCE(NULLIF(ubicacion, ''), ?)
            WHERE id = ?
            """,
            (
                almacen["id"],
                appcc_id,
                _valid_fk(conn, "departamentos", _safe_value(almacen, "departamento_id"), empresa_id),
                _valid_fk(conn, "subdepartamentos", _safe_value(almacen, "subdepartamento_id"), empresa_id),
                _safe_value(almacen, "ubicacion"),
                mantenimiento_id,
            ),
        )
        conn.execute(
            """
            UPDATE equipos
            SET equipo_mantenimiento_id = ?,
                almacen_id = ?,
                centro_id = COALESCE(centro_id, (SELECT centro_id FROM almacenes WHERE id = ?)),
                rango_min = COALESCE(rango_min, ?),
                rango_max = COALESCE(rango_max, ?)
            WHERE id = ?
            """,
            (mantenimiento_id, almacen["id"], almacen["id"], rango_min, rango_max, appcc_id),
        )

    conn.execute(
        """
        UPDATE equipos
        SET centro_id = COALESCE(
            centro_id,
            (SELECT em.centro_id FROM equipos_mantenimiento em WHERE em.id = equipos.equipo_mantenimiento_id),
            (SELECT a.centro_id FROM almacenes a WHERE a.id = equipos.almacen_id),
            (SELECT d.centro_id FROM departamentos d WHERE d.id = equipos.departamento_id),
            (SELECT pv.centro_id FROM puntos_venta pv WHERE pv.id = equipos.punto_venta_id)
        )
        WHERE centro_id IS NULL
        """
    )


def ensure_incidencias_context_schema(conn):
    if not table_exists(conn, "incidencias"):
        return
    for col in [
        "solucion TEXT",
        "fecha_resolucion DATETIME",
        "resuelto_por TEXT",
        "observaciones TEXT",
        "origen_modulo TEXT",
        "destino_modulo TEXT",
        "categoria_operativa TEXT",
        "departamento_id INTEGER",
        "subdepartamento_id INTEGER",
        "punto_venta_id INTEGER",
        "ubicacion TEXT",
        "habitacion_id INTEGER",
        "zona_id INTEGER",
        "equipo_mantenimiento_id INTEGER",
        "equipo_appcc_id INTEGER",
        "orden_trabajo_id INTEGER",
        "requiere_mantenimiento INTEGER DEFAULT 0",
        "requiere_appcc INTEGER DEFAULT 0",
        "origen_tabla TEXT",
        "origen_id INTEGER",
    ]:
        add_column_if_missing(conn, "incidencias", col)

    conn.execute(
        """
        UPDATE incidencias
        SET origen_modulo = CASE
                WHEN COALESCE(tipo, '') LIKE 'pisos%' THEN 'pisos'
                WHEN COALESCE(tipo, '') = 'recepcion' THEN 'compras'
                WHEN COALESCE(tipo, '') LIKE 'appcc%' THEN 'appcc'
                WHEN COALESCE(tipo, '') LIKE 'temperatura%' THEN 'appcc'
                ELSE COALESCE(origen_modulo, 'general')
            END
        WHERE origen_modulo IS NULL OR origen_modulo = ''
        """
    )
    conn.execute(
        """
        UPDATE incidencias
        SET destino_modulo = CASE
                WHEN COALESCE(tipo, '') IN ('pisos_mantenimiento', 'pisos_rotura', 'pisos_seguridad') THEN 'mantenimiento'
                WHEN COALESCE(tipo, '') = 'recepcion' THEN 'compras'
                WHEN COALESCE(tipo, '') LIKE 'appcc%' THEN 'appcc'
                WHEN COALESCE(tipo, '') LIKE 'temperatura%' THEN 'appcc'
                ELSE COALESCE(destino_modulo, origen_modulo, 'general')
            END
        WHERE destino_modulo IS NULL OR destino_modulo = ''
        """
    )
    conn.execute(
        """
        UPDATE incidencias
        SET categoria_operativa = CASE
                WHEN COALESCE(tipo, '') LIKE 'pisos_%' THEN substr(tipo, 7)
                WHEN COALESCE(tipo, '') = 'recepcion' THEN 'recepcion_compra'
                ELSE COALESCE(categoria_operativa, tipo, 'general')
            END
        WHERE categoria_operativa IS NULL OR categoria_operativa = ''
        """
    )
    conn.execute(
        """
        UPDATE incidencias
        SET requiere_mantenimiento = 1
        WHERE COALESCE(tipo, '') IN ('pisos_mantenimiento', 'pisos_rotura', 'pisos_seguridad')
          AND COALESCE(requiere_mantenimiento, 0) = 0
        """
    )
    conn.execute(
        """
        UPDATE incidencias
        SET requiere_appcc = 1
        WHERE (COALESCE(tipo, '') LIKE 'appcc%' OR COALESCE(tipo, '') LIKE 'temperatura%')
          AND COALESCE(requiere_appcc, 0) = 0
        """
    )


def ensure_mantenimiento_context_schema(conn):
    if table_exists(conn, "equipos_mantenimiento"):
        for col in [
            "codigo TEXT",
            "familia TEXT",
            "categoria_operativa TEXT",
            "clase_activo TEXT",
            "criticidad TEXT DEFAULT 'media'",
            "inventariable INTEGER DEFAULT 1",
            "requiere_appcc INTEGER DEFAULT 0",
            "equipo_appcc_id INTEGER",
            "origen_modulo TEXT",
            "habitacion_id INTEGER",
            "zona_id INTEGER",
            "almacen_id INTEGER",
        ]:
            add_column_if_missing(conn, "equipos_mantenimiento", col)
    if table_exists(conn, "ordenes_trabajo"):
        for col in [
            "fecha_inicio DATETIME",
            "fecha_fin DATETIME",
            "horas_reales REAL",
            "coste_real REAL",
            "cerrado_por TEXT",
            "fecha_cierre DATETIME",
            "origen_modulo TEXT",
            "origen_tipo TEXT",
            "origen_id INTEGER",
            "departamento_id INTEGER",
            "subdepartamento_id INTEGER",
            "punto_venta_id INTEGER",
            "ubicacion TEXT",
            "habitacion_id INTEGER",
            "zona_id INTEGER",
            "requiere_appcc INTEGER DEFAULT 0",
            "persistente INTEGER DEFAULT 0",
            "diagnostico TEXT",
            "trabajo_realizado TEXT",
            "resultado TEXT",
            "requiere_seguimiento INTEGER DEFAULT 0",
            "pedido_compra_id INTEGER",
        ]:
            add_column_if_missing(conn, "ordenes_trabajo", col)
        if table_exists(conn, "equipos_mantenimiento"):
            conn.execute(
                """
                UPDATE ordenes_trabajo
                SET departamento_id = COALESCE(departamento_id, (SELECT departamento_id FROM equipos_mantenimiento e WHERE e.id = ordenes_trabajo.equipo_id)),
                    subdepartamento_id = COALESCE(subdepartamento_id, (SELECT subdepartamento_id FROM equipos_mantenimiento e WHERE e.id = ordenes_trabajo.equipo_id)),
                    punto_venta_id = COALESCE(punto_venta_id, (SELECT punto_venta_id FROM equipos_mantenimiento e WHERE e.id = ordenes_trabajo.equipo_id)),
                    ubicacion = COALESCE(NULLIF(ubicacion, ''), (SELECT ubicacion FROM equipos_mantenimiento e WHERE e.id = ordenes_trabajo.equipo_id)),
                    habitacion_id = COALESCE(habitacion_id, (SELECT habitacion_id FROM equipos_mantenimiento e WHERE e.id = ordenes_trabajo.equipo_id)),
                    zona_id = COALESCE(zona_id, (SELECT zona_id FROM equipos_mantenimiento e WHERE e.id = ordenes_trabajo.equipo_id))
                WHERE equipo_id IS NOT NULL
                """
            )
    if table_exists(conn, "incidencias") and table_exists(conn, "ordenes_trabajo"):
        conn.execute(
            """
            UPDATE incidencias
            SET orden_trabajo_id = COALESCE(orden_trabajo_id, (SELECT id FROM ordenes_trabajo ot WHERE ot.incidencia_id = incidencias.id LIMIT 1)),
                equipo_mantenimiento_id = COALESCE(equipo_mantenimiento_id, (SELECT equipo_id FROM ordenes_trabajo ot WHERE ot.incidencia_id = incidencias.id LIMIT 1))
            WHERE EXISTS (SELECT 1 FROM ordenes_trabajo ot WHERE ot.incidencia_id = incidencias.id)
            """
        )

    if table_exists(conn, "historial_mantenimiento"):
        for col in [
            "horas REAL",
            "coste REAL",
            "observaciones TEXT",
            "empresa_id INTEGER DEFAULT 1",
        ]:
            add_column_if_missing(conn, "historial_mantenimiento", col)

    if table_exists(conn, "ordenes_repuestos"):
        for col in [
            "observaciones TEXT",
            "empresa_id INTEGER DEFAULT 1",
            "fecha_uso DATETIME",
            "usuario TEXT",
        ]:
            add_column_if_missing(conn, "ordenes_repuestos", col)

    if table_exists(conn, "repuestos"):
        add_column_if_missing(conn, "repuestos", "producto_id INTEGER")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mantenimiento_pedidos_compra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orden_id INTEGER NOT NULL,
            repuesto_id INTEGER NOT NULL,
            pedido_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            proveedor_id INTEGER,
            cantidad REAL NOT NULL,
            cantidad_recibida_aplicada REAL DEFAULT 0,
            estado TEXT DEFAULT 'solicitado',
            observaciones TEXT,
            usuario TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    ensure_equipos_unificados_schema(conn)

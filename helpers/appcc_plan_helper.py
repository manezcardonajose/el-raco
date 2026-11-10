from helpers.operativa_helper import ensure_incidencias_context_schema


APPCC_CONTROL_DEFAULTS = {
    "REC-01": {
        "area": "Recepcion mercancias",
        "etapa": "Entrada proveedor",
        "tipo_control": "prerrequisito",
        "peligro": "Producto en mal estado, temperatura incorrecta o lote sin identificar",
        "medida_preventiva": "Comprobar estado, temperatura si aplica, lote, caducidad y albaran",
        "limite_critico": "Producto apto, identificado y dentro de temperatura",
        "frecuencia": "Cada recepcion",
        "responsable": "Economato / responsable recepcion",
        "accion_correctiva": "Rechazar, separar, fotografiar y abrir incidencia",
        "verificacion": "Revision compras/economato",
        "modulo_origen": "compras",
    },
    "DESINF-01": {
        "area": "Recepcion / frutas y verduras",
        "etapa": "Desinfeccion",
        "tipo_control": "prerrequisito",
        "peligro": "Contaminacion por vegetales sin desinfectar",
        "medida_preventiva": "Registrar producto, dosis, tiempo de contacto, aclarado y resultado",
        "limite_critico": "Desinfeccion realizada segun procedimiento interno",
        "frecuencia": "Cada lote o recepcion que lo requiera",
        "responsable": "Economato / cocina",
        "accion_correctiva": "Repetir desinfeccion, bloquear lote o desechar",
        "verificacion": "Revision APPCC",
        "modulo_origen": "lotes",
    },
    "DES-01": {
        "area": "Carnes y pescados",
        "etapa": "Descongelacion",
        "tipo_control": "pcc",
        "peligro": "Descongelacion fuera de control",
        "medida_preventiva": "Registrar salida de congelador, camara destino, hora y temperatura",
        "limite_critico": "Proceso en frio controlado y trazable",
        "frecuencia": "Cada salida de congelador",
        "responsable": "Carnes y pescados",
        "accion_correctiva": "Retener lote, revisar temperatura/tiempo y decidir aptitud",
        "verificacion": "Revision produccion/APPCC",
        "modulo_origen": "lotes",
    },
    "ELAB-01": {
        "area": "Produccion cocina",
        "etapa": "Elaboracion / coccion",
        "tipo_control": "pcc",
        "peligro": "Coccion insuficiente o manipulacion incorrecta",
        "medida_preventiva": "Registrar tiempos, temperaturas y lotes usados",
        "limite_critico": "Temperatura segura segun producto y ficha tecnica",
        "frecuencia": "Cada elaboracion critica",
        "responsable": "Jefe partida / cocina",
        "accion_correctiva": "Continuar coccion, separar producto o desechar",
        "verificacion": "Revision jefe cocina",
        "modulo_origen": "produccion",
    },
    "ABAT-01": {
        "area": "Produccion cocina",
        "etapa": "Abatimiento / enfriamiento",
        "tipo_control": "pcc",
        "peligro": "Enfriamiento lento y proliferacion microbiologica",
        "medida_preventiva": "Registrar temperatura inicial, final, hora inicio y fin",
        "limite_critico": "Bajada controlada segun procedimiento interno",
        "frecuencia": "Cada abatimiento",
        "responsable": "Cocina",
        "accion_correctiva": "Prolongar proceso, separar o desechar",
        "verificacion": "Revision APPCC",
        "modulo_origen": "produccion",
    },
    "REGEN-01": {
        "area": "Produccion cocina",
        "etapa": "Regeneracion",
        "tipo_control": "pcc",
        "peligro": "Regeneracion insuficiente antes del servicio",
        "medida_preventiva": "Registrar temperatura y tiempo de regeneracion",
        "limite_critico": "Regeneracion controlada segun ficha tecnica",
        "frecuencia": "Cada regeneracion",
        "responsable": "Cocina",
        "accion_correctiva": "Continuar regeneracion, retirar o desechar",
        "verificacion": "Revision jefe cocina",
        "modulo_origen": "produccion",
    },
    "CAL-01": {
        "area": "Servicio buffet",
        "etapa": "Mantenimiento caliente",
        "tipo_control": "pcc",
        "peligro": "Temperatura de servicio insuficiente",
        "medida_preventiva": "Controlar temperatura de platos calientes en servicio",
        "limite_critico": ">= 65 C salvo criterio tecnico documentado",
        "frecuencia": "Cada servicio",
        "responsable": "Responsable buffet",
        "accion_correctiva": "Regenerar, retirar o desechar",
        "verificacion": "Revision jefe cocina",
        "modulo_origen": "produccion",
    },
    "MERMA-01": {
        "area": "Stock y produccion",
        "etapa": "Deterioro / merma / desecho",
        "tipo_control": "prerrequisito",
        "peligro": "Producto no apto reutilizado o sin justificar",
        "medida_preventiva": "Registrar causa, lote, cantidad, ubicacion y destino",
        "limite_critico": "Producto no apto separado y trazado",
        "frecuencia": "Cada merma o deterioro",
        "responsable": "Responsable departamento",
        "accion_correctiva": "Desechar, bloquear lote y abrir incidencia si procede",
        "verificacion": "Revision APPCC/economato",
        "modulo_origen": "stock",
    },
}


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn, table_name, column_name):
    try:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return False
    return any((col["name"] if hasattr(col, "keys") and "name" in col.keys() else col[1]) == column_name for col in cols)


def _add_column_if_missing(conn, table_name, column_sql):
    column_name = column_sql.strip().split()[0]
    if _table_exists(conn, table_name) and not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def ensure_appcc_plan_schema(conn):
    ensure_incidencias_context_schema(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS appcc_controles_plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            codigo TEXT,
            area TEXT NOT NULL,
            etapa TEXT,
            tipo_control TEXT DEFAULT 'prerrequisito',
            peligro TEXT,
            medida_preventiva TEXT,
            limite_critico TEXT,
            frecuencia TEXT,
            responsable TEXT,
            accion_correctiva TEXT,
            verificacion TEXT,
            modulo_origen TEXT,
            activo INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS appcc_registros_plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            control_id INTEGER NOT NULL,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP,
            valor TEXT,
            resultado TEXT DEFAULT 'ok',
            realizado_por TEXT,
            observaciones TEXT,
            accion_correctiva TEXT,
            verificado_por TEXT,
            fecha_verificacion TEXT,
            incidencia_id INTEGER,
            origen_tabla TEXT,
            origen_id INTEGER,
            codigo_operacion TEXT,
            FOREIGN KEY (control_id) REFERENCES appcc_controles_plan(id)
        )
    """)
    for col in [
        "empresa_id INTEGER DEFAULT 1",
        "codigo TEXT",
        "area TEXT",
        "etapa TEXT",
        "tipo_control TEXT DEFAULT 'prerrequisito'",
        "peligro TEXT",
        "medida_preventiva TEXT",
        "limite_critico TEXT",
        "frecuencia TEXT",
        "responsable TEXT",
        "accion_correctiva TEXT",
        "verificacion TEXT",
        "modulo_origen TEXT",
        "activo INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "appcc_controles_plan", col)
    for col in [
        "empresa_id INTEGER DEFAULT 1",
        "control_id INTEGER",
        "fecha TEXT DEFAULT CURRENT_TIMESTAMP",
        "valor TEXT",
        "resultado TEXT DEFAULT 'ok'",
        "realizado_por TEXT",
        "observaciones TEXT",
        "accion_correctiva TEXT",
        "verificado_por TEXT",
        "fecha_verificacion TEXT",
        "incidencia_id INTEGER",
        "origen_tabla TEXT",
        "origen_id INTEGER",
        "codigo_operacion TEXT",
    ]:
        _add_column_if_missing(conn, "appcc_registros_plan", col)


def ensure_appcc_control(conn, empresa_id, codigo, defaults=None):
    ensure_appcc_plan_schema(conn)
    control = conn.execute(
        """
        SELECT *
        FROM appcc_controles_plan
        WHERE empresa_id = ? AND codigo = ?
        LIMIT 1
        """,
        (empresa_id, codigo),
    ).fetchone()
    if control:
        return control

    data = dict(APPCC_CONTROL_DEFAULTS.get(codigo, {}))
    if defaults:
        data.update(defaults)
    data.setdefault("area", "APPCC")
    data.setdefault("tipo_control", "prerrequisito")
    conn.execute(
        """
        INSERT INTO appcc_controles_plan (
            empresa_id, codigo, area, etapa, tipo_control, peligro, medida_preventiva,
            limite_critico, frecuencia, responsable, accion_correctiva, verificacion,
            modulo_origen, activo
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            empresa_id,
            codigo,
            data.get("area"),
            data.get("etapa"),
            data.get("tipo_control"),
            data.get("peligro"),
            data.get("medida_preventiva"),
            data.get("limite_critico"),
            data.get("frecuencia"),
            data.get("responsable"),
            data.get("accion_correctiva"),
            data.get("verificacion"),
            data.get("modulo_origen"),
        ),
    )
    return conn.execute(
        "SELECT * FROM appcc_controles_plan WHERE empresa_id = ? AND codigo = ? LIMIT 1",
        (empresa_id, codigo),
    ).fetchone()


def registrar_appcc_control(
    conn,
    empresa_id,
    codigo,
    valor=None,
    resultado="ok",
    usuario="admin",
    observaciones="",
    accion_correctiva="",
    origen_tabla=None,
    origen_id=None,
    codigo_operacion=None,
    incidencia_id=None,
    defaults=None,
):
    control = ensure_appcc_control(conn, empresa_id, codigo, defaults=defaults)
    resultado = resultado or "ok"
    usuario = usuario or "admin"

    registro = None
    if codigo_operacion and origen_tabla and origen_id:
        registro = conn.execute(
            """
            SELECT r.*
            FROM appcc_registros_plan r
            WHERE r.empresa_id = ?
              AND r.control_id = ?
              AND r.origen_tabla = ?
              AND r.origen_id = ?
              AND r.codigo_operacion = ?
            LIMIT 1
            """,
            (empresa_id, control["id"], origen_tabla, origen_id, codigo_operacion),
        ).fetchone()

    if registro:
        conn.execute(
            """
            UPDATE appcc_registros_plan
            SET fecha = CURRENT_TIMESTAMP,
                valor = ?,
                resultado = ?,
                realizado_por = ?,
                observaciones = ?,
                accion_correctiva = ?,
                incidencia_id = COALESCE(?, incidencia_id)
            WHERE id = ?
            """,
            (valor, resultado, usuario, observaciones, accion_correctiva, incidencia_id, registro["id"]),
        )
        registro_id = registro["id"]
        incidencia_id = incidencia_id or (registro["incidencia_id"] if "incidencia_id" in registro.keys() else None)
    else:
        cursor = conn.execute(
            """
            INSERT INTO appcc_registros_plan (
                empresa_id, control_id, valor, resultado, realizado_por, observaciones,
                accion_correctiva, origen_tabla, origen_id, codigo_operacion
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                empresa_id,
                control["id"],
                valor,
                resultado,
                usuario,
                observaciones,
                accion_correctiva,
                origen_tabla,
                origen_id,
                codigo_operacion,
            ),
        )
        registro_id = cursor.lastrowid
        if incidencia_id:
            conn.execute(
                "UPDATE appcc_registros_plan SET incidencia_id = ? WHERE id = ?",
                (incidencia_id, registro_id),
            )

    if incidencia_id:
        conn.execute(
            """
            UPDATE incidencias
            SET requiere_appcc = 1,
                origen_modulo = COALESCE(NULLIF(origen_modulo, ''), 'appcc'),
                destino_modulo = COALESCE(NULLIF(destino_modulo, ''), 'appcc'),
                categoria_operativa = COALESCE(NULLIF(categoria_operativa, ''), ?),
                origen_tabla = COALESCE(NULLIF(origen_tabla, ''), ?),
                origen_id = COALESCE(origen_id, ?)
            WHERE id = ?
            """,
            (
                control["tipo_control"] or "appcc",
                origen_tabla or "appcc_registros_plan",
                origen_id or registro_id,
                incidencia_id,
            ),
        )

    if resultado != "ok" and not incidencia_id:
        descripcion = (
            f"No conformidad APPCC {codigo}: {control['area'] or ''} "
            f"{control['etapa'] or ''}. Valor: {valor or '-'}"
        ).strip()
        if observaciones:
            descripcion += f". {observaciones}"
        cursor = conn.execute(
            """
            INSERT INTO incidencias (
                tipo, referencia_id, usuario, descripcion, gravedad, estado,
                origen_modulo, destino_modulo, categoria_operativa, requiere_appcc,
                origen_tabla, origen_id, empresa_id
            )
            VALUES (?, ?, ?, ?, ?, 'abierta', 'appcc', 'appcc', ?, 1, ?, ?, ?)
            """,
            (
                "appcc_control",
                registro_id,
                usuario,
                descripcion,
                "alta" if control["tipo_control"] == "pcc" else "media",
                control["tipo_control"] or "appcc",
                origen_tabla or "appcc_registros_plan",
                origen_id or registro_id,
                empresa_id,
            ),
        )
        incidencia_id = cursor.lastrowid
        conn.execute(
            "UPDATE appcc_registros_plan SET incidencia_id = ? WHERE id = ?",
            (incidencia_id, registro_id),
        )

    return registro_id

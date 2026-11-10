# blueprints/appcc/routes.py
from flask import render_template, request, redirect, url_for, session, flash
from database import get_db
from datetime import datetime, timedelta
from . import appcc_bp
from blueprints.auth.routes import (
    login_required,
    usuario_departamento_id,
    usuario_es_global_operacion,
    usuario_subdepartamento_id,
    usuario_tiene_permiso,
)
from helpers.operativa_helper import ensure_equipos_unificados_schema, ensure_incidencias_context_schema
from helpers.centros_helper import (
    asignar_centro_desde_departamento,
    centro_activo_id,
    ids_departamentos_centro,
    ids_puntos_venta_centro,
)


ROLES_OPERATIVA_COCINA = {
    "admin",
    "administrador",
    "gerente",
    "director",
    "direccion",
    "direccion_general",
    "director_operaciones",
    "director_hotel",
    "subdirector",
    "propietario",
    "jefe_cocina",
    "chef",
    "jefe_partida",
    "cocinero",
    "ayudante_cocina",
}


def _rol_actual_key():
    texto = str(session.get("user_rol") or "").strip().lower()
    for origen, destino in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        texto = texto.replace(origen, destino)
    for separador in (" ", "-", "/", "."):
        texto = texto.replace(separador, "_")
    while "__" in texto:
        texto = texto.replace("__", "_")
    return texto.strip("_")


def _puede_gestionar_appcc():
    return (
        usuario_es_global_operacion()
        or usuario_tiene_permiso("appcc_gestionar")
        or _rol_actual_key() in ROLES_OPERATIVA_COCINA
    )


def _requiere_gestion_appcc(conn=None):
    if _puede_gestionar_appcc():
        return None
    if conn:
        conn.close()
    flash("No tienes permisos para modificar registros APPCC.", "warning")
    return redirect(url_for("appcc.appcc_dashboard"))


def _requiere_centro_appcc(conn=None):
    if centro_activo_id():
        return None
    if conn:
        conn.close()
    flash("Selecciona un centro antes de modificar APPCC.", "warning")
    return redirect(url_for("rrhh.centro_empresa_general"))


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
    return any(col[1] == column_name for col in cols)


def _add_column_if_missing(conn, table_name, column_sql):
    column_name = column_sql.strip().split()[0]
    if _table_exists(conn, table_name) and not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def _ids_departamentos_appcc(conn, empresa_id, centro_id=None):
    centro_id = centro_id if centro_id is not None else centro_activo_id()
    if usuario_es_global_operacion():
        return ids_departamentos_centro(conn, empresa_id, centro_id) if centro_id else []
    departamento_id = usuario_departamento_id()
    if not departamento_id:
        return []
    if centro_id and _column_exists(conn, "departamentos", "centro_id"):
        row = conn.execute(
            """
            SELECT id
            FROM departamentos
            WHERE id = ? AND empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1
            """,
            (departamento_id, empresa_id, centro_id),
        ).fetchone()
        return [departamento_id] if row else []
    return [departamento_id]


def _ids_subdepartamentos_appcc(conn, empresa_id, centro_id=None, dep_ids=None):
    if usuario_es_global_operacion():
        return []
    subdepartamento_id = usuario_subdepartamento_id()
    if not subdepartamento_id:
        return []
    dep_ids = dep_ids or _ids_departamentos_appcc(conn, empresa_id, centro_id)
    if not dep_ids or not _column_exists(conn, "subdepartamentos", "departamento_id"):
        return []
    centro_sql = " AND centro_id = ?" if centro_id and _column_exists(conn, "subdepartamentos", "centro_id") else ""
    params = [subdepartamento_id, empresa_id] + dep_ids + ([centro_id] if centro_sql else [])
    row = conn.execute(
        f"""
        SELECT id
        FROM subdepartamentos
        WHERE id = ?
          AND empresa_id = ?
          AND departamento_id IN ({','.join('?' for _ in dep_ids)})
          {centro_sql}
          AND COALESCE(activo, 1) = 1
        """,
        params,
    ).fetchone()
    return [subdepartamento_id] if row else []


def _ids_puntos_venta_appcc(conn, empresa_id, centro_id=None, dep_ids=None):
    centro_id = centro_id if centro_id is not None else centro_activo_id()
    if usuario_es_global_operacion():
        return ids_puntos_venta_centro(conn, empresa_id, centro_id) if centro_id else []
    dep_ids = dep_ids or _ids_departamentos_appcc(conn, empresa_id, centro_id)
    subdep_ids = _ids_subdepartamentos_appcc(conn, empresa_id, centro_id, dep_ids)
    if not dep_ids or not _column_exists(conn, "puntos_venta", "departamento_id"):
        return []
    centro_sql = " AND centro_id = ?" if centro_id and _column_exists(conn, "puntos_venta", "centro_id") else ""
    subdep_sql = ""
    params = [empresa_id] + dep_ids
    if subdep_ids and _column_exists(conn, "puntos_venta", "subdepartamento_id"):
        subdep_sql = f" AND subdepartamento_id IN ({','.join('?' for _ in subdep_ids)})"
        params.extend(subdep_ids)
    params.extend([centro_id] if centro_sql else [])
    return [
        row["id"]
        for row in conn.execute(
            f"""
            SELECT id
            FROM puntos_venta
            WHERE empresa_id = ?
              AND departamento_id IN ({','.join('?' for _ in dep_ids)})
              {subdep_sql}
              {centro_sql}
              AND COALESCE(activo, 1) = 1
            """,
            params,
        ).fetchall()
    ]


def _filtro_appcc_centro(conn, empresa_id, alias="c"):
    centro_id = centro_activo_id()
    if not centro_id and usuario_es_global_operacion():
        return "", []
    dep_ids = _ids_departamentos_appcc(conn, empresa_id, centro_id)
    subdep_ids = _ids_subdepartamentos_appcc(conn, empresa_id, centro_id, dep_ids)
    condiciones = []
    params = []
    if usuario_es_global_operacion() and centro_id and _column_exists(conn, "appcc_controles_plan", "centro_id"):
        condiciones.append(f"{alias}.centro_id = ?")
        params.append(centro_id)
    elif subdep_ids and _column_exists(conn, "appcc_controles_plan", "subdepartamento_id"):
        condiciones.append(f"{alias}.subdepartamento_id IN ({','.join('?' for _ in subdep_ids)})")
        params.extend(subdep_ids)
    elif _column_exists(conn, "appcc_controles_plan", "departamento_id") and dep_ids:
        condiciones.append(f"{alias}.departamento_id IN ({','.join('?' for _ in dep_ids)})")
        params.extend(dep_ids)
    if dep_ids:
        condiciones.extend([f"{alias}.codigo LIKE ?" for _ in dep_ids])
        params.extend([f"{dep_id}-%" for dep_id in dep_ids])
    if not condiciones:
        return " AND 1 = 0", []
    return f" AND ({' OR '.join(condiciones)})", params


def _filtro_lotes_centro(conn, empresa_id, alias="l"):
    centro_id = centro_activo_id()
    if not centro_id and usuario_es_global_operacion():
        return "", []
    dep_ids = _ids_departamentos_appcc(conn, empresa_id, centro_id)
    subdep_ids = _ids_subdepartamentos_appcc(conn, empresa_id, centro_id, dep_ids)
    pv_ids = _ids_puntos_venta_appcc(conn, empresa_id, centro_id, dep_ids)
    condiciones = []
    params = []
    if subdep_ids and _column_exists(conn, "almacenes", "subdepartamento_id"):
        condiciones.append(f"{alias}.almacen_id IN (SELECT id FROM almacenes WHERE subdepartamento_id IN ({','.join('?' for _ in subdep_ids)}))")
        params.extend(subdep_ids)
    elif dep_ids:
        condiciones.append(f"{alias}.almacen_id IN (SELECT id FROM almacenes WHERE departamento_id IN ({','.join('?' for _ in dep_ids)}))")
        params.extend(dep_ids)
    if pv_ids:
        condiciones.append(f"{alias}.almacen_id IN (SELECT almacen_id FROM puntos_venta WHERE id IN ({','.join('?' for _ in pv_ids)}) AND almacen_id IS NOT NULL)")
        params.extend(pv_ids)
    if not condiciones:
        return " AND 1 = 0", []
    return f" AND ({' OR '.join(condiciones)})", params


def _filtro_equipos_centro(conn, empresa_id, alias="e"):
    centro_id = centro_activo_id()
    if not centro_id and usuario_es_global_operacion():
        return "", []
    dep_ids = _ids_departamentos_appcc(conn, empresa_id, centro_id)
    subdep_ids = _ids_subdepartamentos_appcc(conn, empresa_id, centro_id, dep_ids)
    pv_ids = _ids_puntos_venta_appcc(conn, empresa_id, centro_id, dep_ids)
    condiciones = []
    params = []
    if usuario_es_global_operacion() and centro_id and _column_exists(conn, "equipos", "centro_id"):
        condiciones.append(f"{alias}.centro_id = ?")
        params.append(centro_id)
    if subdep_ids and _column_exists(conn, "equipos", "subdepartamento_id"):
        condiciones.append(f"{alias}.subdepartamento_id IN ({','.join('?' for _ in subdep_ids)})")
        params.extend(subdep_ids)
    elif dep_ids and _column_exists(conn, "equipos", "departamento_id"):
        condiciones.append(f"{alias}.departamento_id IN ({','.join('?' for _ in dep_ids)})")
        params.extend(dep_ids)
    if pv_ids and _column_exists(conn, "equipos", "punto_venta_id"):
        condiciones.append(f"{alias}.punto_venta_id IN ({','.join('?' for _ in pv_ids)})")
        params.extend(pv_ids)
    if _column_exists(conn, "equipos", "almacen_id") and _column_exists(conn, "almacenes", "subdepartamento_id") and subdep_ids:
        condiciones.append(f"{alias}.almacen_id IN (SELECT id FROM almacenes WHERE empresa_id = ? AND subdepartamento_id IN ({','.join('?' for _ in subdep_ids)}))")
        params.extend([empresa_id] + subdep_ids)
    elif _column_exists(conn, "equipos", "almacen_id") and _column_exists(conn, "almacenes", "departamento_id") and dep_ids:
        condiciones.append(f"{alias}.almacen_id IN (SELECT id FROM almacenes WHERE empresa_id = ? AND departamento_id IN ({','.join('?' for _ in dep_ids)}))")
        params.extend([empresa_id] + dep_ids)
    if not condiciones:
        return " AND 1 = 0", []
    return f" AND ({' OR '.join(condiciones)})", params


def _filtro_plan_limpieza_centro(conn, empresa_id, alias="pl"):
    centro_id = centro_activo_id()
    if not centro_id and usuario_es_global_operacion():
        return "", []
    dep_ids = _ids_departamentos_appcc(conn, empresa_id, centro_id)
    subdep_ids = _ids_subdepartamentos_appcc(conn, empresa_id, centro_id, dep_ids)
    pv_ids = _ids_puntos_venta_appcc(conn, empresa_id, centro_id, dep_ids)
    condiciones = []
    params = []
    if usuario_es_global_operacion() and centro_id and _column_exists(conn, "plan_limpieza", "centro_id"):
        condiciones.append(f"{alias}.centro_id = ?")
        params.append(centro_id)
    if subdep_ids and _column_exists(conn, "plan_limpieza", "subdepartamento_id"):
        condiciones.append(f"{alias}.subdepartamento_id IN ({','.join('?' for _ in subdep_ids)})")
        params.extend(subdep_ids)
    elif dep_ids and _column_exists(conn, "plan_limpieza", "departamento_id"):
        condiciones.append(f"{alias}.departamento_id IN ({','.join('?' for _ in dep_ids)})")
        params.extend(dep_ids)
    if pv_ids and _column_exists(conn, "plan_limpieza", "punto_venta_id"):
        condiciones.append(f"{alias}.punto_venta_id IN ({','.join('?' for _ in pv_ids)})")
        params.extend(pv_ids)
    if not condiciones:
        return " AND 1 = 0", []
    return f" AND ({' OR '.join(condiciones)})", params


def _filtro_incidencias_centro(conn, empresa_id, alias="i"):
    centro_id = centro_activo_id()
    if not centro_id and usuario_es_global_operacion():
        return "", []
    dep_ids = _ids_departamentos_appcc(conn, empresa_id, centro_id)
    subdep_ids = _ids_subdepartamentos_appcc(conn, empresa_id, centro_id, dep_ids)
    pv_ids = _ids_puntos_venta_appcc(conn, empresa_id, centro_id, dep_ids)
    condiciones = []
    params = []
    if subdep_ids and _column_exists(conn, "incidencias", "subdepartamento_id"):
        condiciones.append(f"{alias}.subdepartamento_id IN ({','.join('?' for _ in subdep_ids)})")
        params.extend(subdep_ids)
    elif dep_ids and _column_exists(conn, "incidencias", "departamento_id"):
        condiciones.append(f"{alias}.departamento_id IN ({','.join('?' for _ in dep_ids)})")
        params.extend(dep_ids)
    if pv_ids and _column_exists(conn, "incidencias", "punto_venta_id"):
        condiciones.append(f"{alias}.punto_venta_id IN ({','.join('?' for _ in pv_ids)})")
        params.extend(pv_ids)
    if _column_exists(conn, "incidencias", "equipo_appcc_id") and _column_exists(conn, "equipos", "departamento_id") and dep_ids:
        condiciones.append(f"{alias}.equipo_appcc_id IN (SELECT id FROM equipos WHERE empresa_id = ? AND departamento_id IN ({','.join('?' for _ in dep_ids)}))")
        params.extend([empresa_id] + dep_ids)
    if not condiciones:
        return " AND 1 = 0", []
    return f" AND ({' OR '.join(condiciones)})", params


def _centro_sql_simple(conn, table_name, alias):
    centro_id = centro_activo_id()
    if centro_id and _column_exists(conn, table_name, "centro_id"):
        return f" AND {alias}.centro_id = ?", [centro_id]
    return "", []


def _to_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _safe_scalar(conn, sql, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        value = row[0]
        return default if value is None else value
    except Exception:
        return default


def _safe_rows(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def _tables_exist(conn, *table_names):
    return all(_table_exists(conn, name) for name in table_names)


APPCC_PLAN_BASE = [
    {
        "codigo": "REC-01",
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
    {
        "codigo": "FRIO-01",
        "area": "Camaras y almacenes frios",
        "etapa": "Conservacion",
        "tipo_control": "pcc",
        "peligro": "Crecimiento microbiologico por temperatura fuera de rango",
        "medida_preventiva": "Registrar temperatura de camaras y equipos criticos",
        "limite_critico": "Refrigerado 0/5 C, congelado <= -18 C o rango configurado",
        "frecuencia": "Diaria / por turno",
        "responsable": "Jefe departamento",
        "accion_correctiva": "Comprobar equipo, mover producto, abrir incidencia y mantenimiento si procede",
        "verificacion": "Revision semanal APPCC",
        "modulo_origen": "appcc",
    },
    {
        "codigo": "DES-01",
        "area": "Congelados de produccion",
        "etapa": "Descongelacion",
        "tipo_control": "pcc",
        "peligro": "Descongelacion fuera de control",
        "medida_preventiva": "Registrar salida de congelador, camara destino, hora y temperatura",
        "limite_critico": "Proceso en frio controlado y trazable",
        "frecuencia": "Cada salida de congelador",
        "responsable": "Produccion",
        "accion_correctiva": "Retener lote, revisar temperatura/tiempo y decidir aptitud",
        "verificacion": "Revision produccion/APPCC",
        "modulo_origen": "lotes",
    },
    {
        "codigo": "DESINF-01",
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
    {
        "codigo": "ELAB-01",
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
    {
        "codigo": "ABAT-01",
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
    {
        "codigo": "REGEN-01",
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
    {
        "codigo": "CAL-01",
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
    {
        "codigo": "LIMP-01",
        "area": "Limpieza y desinfeccion",
        "etapa": "Zonas, equipos y utensilios",
        "tipo_control": "prerrequisito",
        "peligro": "Contaminacion cruzada por limpieza deficiente",
        "medida_preventiva": "Plan de limpieza por area, producto, responsable y frecuencia",
        "limite_critico": "Registro realizado y verificado",
        "frecuencia": "Segun plan",
        "responsable": "Cada departamento",
        "accion_correctiva": "Repetir limpieza, cambiar producto o abrir incidencia",
        "verificacion": "Revision responsable APPCC",
        "modulo_origen": "appcc",
    },
    {
        "codigo": "ACEITE-01",
        "area": "Cocina caliente",
        "etapa": "Frituras",
        "tipo_control": "prerrequisito",
        "peligro": "Aceite degradado",
        "medida_preventiva": "Registrar control/cambio de aceite y resultado",
        "limite_critico": "Dentro del criterio interno o tiras de control",
        "frecuencia": "Segun uso / diario",
        "responsable": "Cocina caliente",
        "accion_correctiva": "Filtrar o cambiar aceite",
        "verificacion": "Revision jefe cocina",
        "modulo_origen": "appcc",
    },
    {
        "codigo": "ALERG-01",
        "area": "Buffet y carta",
        "etapa": "Informacion al cliente",
        "tipo_control": "prerrequisito",
        "peligro": "Informacion incorrecta de alergenos",
        "medida_preventiva": "Tarjetas desde produccion real, recetas y productos con alergenos",
        "limite_critico": "Tarjeta validada antes del servicio",
        "frecuencia": "Cada servicio",
        "responsable": "Jefe cocina / sala",
        "accion_correctiva": "Retirar tarjeta/plato hasta validar informacion",
        "verificacion": "Revision servicio",
        "modulo_origen": "produccion",
    },
    {
        "codigo": "MERMA-01",
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
]


APPCC_ORIGEN_LABELS = {
    "manual": "Registro manual",
    "lotes": "Recepcion y lotes",
    "registros_descongelado": "Descongelacion",
    "registros_desinfeccion": "Desinfeccion",
    "registros_deterioro_lotes": "Deterioro de lotes",
    "mermas": "Mermas",
    "lineas_produccion": "Produccion",
    "appcc_registros_plan": "Plan APPCC",
}


def _origen_label(origen):
    clave = (origen or "manual").strip() or "manual"
    return APPCC_ORIGEN_LABELS.get(clave, clave.replace("_", " ").title())


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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS appcc_cierres_diarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            fecha TEXT NOT NULL,
            resultado TEXT DEFAULT 'ok',
            responsable TEXT,
            observaciones TEXT,
            resumen TEXT,
            creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
            actualizado_en TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for col in [
        "empresa_id INTEGER DEFAULT 1",
        "centro_id INTEGER",
        "departamento_id INTEGER",
        "subdepartamento_id INTEGER",
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
    for col in [
        "empresa_id INTEGER DEFAULT 1",
        "centro_id INTEGER",
        "fecha TEXT",
        "resultado TEXT DEFAULT 'ok'",
        "responsable TEXT",
        "observaciones TEXT",
        "resumen TEXT",
        "creado_en TEXT DEFAULT CURRENT_TIMESTAMP",
        "actualizado_en TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "appcc_cierres_diarios", col)


def ensure_alertas_caducidad_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alertas_caducidad (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha_alerta DATE,
            tipo TEXT,
            notificado INTEGER DEFAULT 0,
            resuelto INTEGER DEFAULT 0,
            empresa_id INTEGER DEFAULT 1
        )
    """)
    for col in [
        "lote_id INTEGER",
        "fecha_alerta DATE",
        "tipo TEXT",
        "notificado INTEGER DEFAULT 0",
        "resuelto INTEGER DEFAULT 0",
        "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "alertas_caducidad", col)


def _seed_appcc_plan_base(conn, empresa_id):
    ensure_appcc_plan_schema(conn)
    creados = 0
    centro_id = centro_activo_id()
    for item in APPCC_PLAN_BASE:
        existe = conn.execute(
            """
            SELECT id FROM appcc_controles_plan
            WHERE empresa_id = ? AND codigo = ?
              AND COALESCE(centro_id, 0) = COALESCE(?, 0)
            LIMIT 1
            """,
            (empresa_id, item["codigo"], centro_id),
        ).fetchone()
        if existe:
            continue
        conn.execute(
            """
            INSERT INTO appcc_controles_plan
            (empresa_id, codigo, area, etapa, tipo_control, peligro, medida_preventiva,
             limite_critico, frecuencia, responsable, accion_correctiva, verificacion,
             modulo_origen, centro_id, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                empresa_id,
                item["codigo"],
                item["area"],
                item["etapa"],
                item["tipo_control"],
                item["peligro"],
                item["medida_preventiva"],
                item["limite_critico"],
                item["frecuencia"],
                item["responsable"],
                item["accion_correctiva"],
                item["verificacion"],
                item["modulo_origen"],
                centro_id,
            ),
        )
        creados += 1
    return creados


def _appcc_control_defaults(codigo):
    for item in APPCC_PLAN_BASE:
        if item["codigo"] == codigo:
            return item
    return {}


def _registrar_operacion_appcc(
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
):
    ensure_appcc_plan_schema(conn)
    centro_id = centro_activo_id()
    departamento_id = None if usuario_es_global_operacion() else usuario_departamento_id()
    subdepartamento_id = None if usuario_es_global_operacion() else usuario_subdepartamento_id()
    codigo_busqueda = f"{departamento_id}-{codigo}" if departamento_id else codigo
    control = conn.execute(
        """
        SELECT *
        FROM appcc_controles_plan
        WHERE empresa_id = ? AND codigo = ?
          AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        LIMIT 1
        """,
        (empresa_id, codigo_busqueda, centro_id),
    ).fetchone()
    if not control:
        if departamento_id:
            defaults = _appcc_control_defaults(codigo)
            conn.execute(
                """
                INSERT INTO appcc_controles_plan
                (empresa_id, centro_id, departamento_id, subdepartamento_id, codigo, area, etapa, tipo_control,
                 peligro, medida_preventiva, limite_critico, frecuencia, responsable,
                 accion_correctiva, verificacion, modulo_origen, activo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    empresa_id,
                    centro_id,
                    departamento_id,
                    subdepartamento_id,
                    codigo_busqueda,
                    defaults.get("area") or "APPCC departamento",
                    defaults.get("etapa") or codigo,
                    defaults.get("tipo_control") or "prerrequisito",
                    defaults.get("peligro"),
                    defaults.get("medida_preventiva"),
                    defaults.get("limite_critico"),
                    defaults.get("frecuencia"),
                    defaults.get("responsable"),
                    defaults.get("accion_correctiva"),
                    defaults.get("verificacion"),
                    defaults.get("modulo_origen") or "appcc",
                ),
            )
        else:
            _seed_appcc_plan_base(conn, empresa_id)
        control = conn.execute(
            """
            SELECT *
            FROM appcc_controles_plan
            WHERE empresa_id = ? AND codigo = ?
              AND COALESCE(centro_id, 0) = COALESCE(?, 0)
            LIMIT 1
            """,
            (empresa_id, codigo_busqueda, centro_id),
        ).fetchone()
    if not control:
        return None

    if codigo_operacion and origen_tabla and origen_id:
        existente = conn.execute(
            """
            SELECT id
            FROM appcc_registros_plan
            WHERE empresa_id = ?
              AND control_id = ?
              AND origen_tabla = ?
              AND origen_id = ?
              AND codigo_operacion = ?
            LIMIT 1
            """,
            (empresa_id, control["id"], origen_tabla, origen_id, codigo_operacion),
        ).fetchone()
        if existente:
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
                (
                    valor,
                    resultado or "ok",
                    usuario or "admin",
                    observaciones,
                    accion_correctiva,
                    incidencia_id,
                    existente["id"],
                ),
            )
            return existente["id"]

    cursor = conn.execute(
        """
        INSERT INTO appcc_registros_plan
        (empresa_id, control_id, valor, resultado, realizado_por, observaciones,
         accion_correctiva, incidencia_id, origen_tabla, origen_id, codigo_operacion)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            empresa_id,
            control["id"],
            valor,
            resultado or "ok",
            usuario or "admin",
            observaciones,
            accion_correctiva,
            incidencia_id,
            origen_tabla,
            origen_id,
            codigo_operacion,
        ),
    )
    return cursor.lastrowid


def _resumen_appcc_diario(conn, empresa_id, fecha):
    ensure_appcc_plan_schema(conn)
    _seed_appcc_plan_base(conn, empresa_id)
    appcc_centro_sql, appcc_centro_params = _filtro_appcc_centro(conn, empresa_id, "c")
    lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, empresa_id, "l")
    equipos_centro_sql, equipos_centro_params = _filtro_equipos_centro(conn, empresa_id, "e")
    limpieza_centro_sql, limpieza_centro_params = _filtro_plan_limpieza_centro(conn, empresa_id, "pl")
    incidencias_centro_sql, incidencias_centro_params = _filtro_incidencias_centro(conn, empresa_id, "i")
    cierre_centro_sql, cierre_centro_params = _centro_sql_simple(conn, "appcc_cierres_diarios", "appcc_cierres_diarios")

    controles = _safe_rows(
        conn,
        f"""
        SELECT c.*,
               COUNT(r.id) AS registros_hoy,
               SUM(CASE WHEN COALESCE(r.resultado, 'ok') != 'ok' THEN 1 ELSE 0 END) AS no_ok_hoy,
               SUM(CASE WHEN COALESCE(r.verificado_por, '') = '' THEN 1 ELSE 0 END) AS sin_verificar_hoy,
               (
                   SELECT MAX(r2.fecha)
                   FROM appcc_registros_plan r2
                   WHERE r2.control_id = c.id AND r2.empresa_id = c.empresa_id
               ) AS ultimo_registro
        FROM appcc_controles_plan c
        LEFT JOIN appcc_registros_plan r
               ON r.control_id = c.id
              AND r.empresa_id = c.empresa_id
              AND date(r.fecha) = ?
        WHERE c.empresa_id = ? AND COALESCE(c.activo, 1) = 1
          {appcc_centro_sql}
        GROUP BY c.id
        ORDER BY
            CASE c.tipo_control WHEN 'pcc' THEN 0 ELSE 1 END,
            c.area,
            c.etapa,
            c.codigo
        """,
        [fecha, empresa_id] + appcc_centro_params,
    )

    registros_hoy = _safe_scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM appcc_registros_plan r
        JOIN appcc_controles_plan c ON c.id = r.control_id
        WHERE r.empresa_id = ? AND date(r.fecha) = ? {appcc_centro_sql}
        """,
        [empresa_id, fecha] + appcc_centro_params,
    )
    no_ok_hoy = _safe_scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM appcc_registros_plan r
        JOIN appcc_controles_plan c ON c.id = r.control_id
        WHERE r.empresa_id = ? AND date(r.fecha) = ? AND COALESCE(r.resultado, 'ok') != 'ok' {appcc_centro_sql}
        """,
        [empresa_id, fecha] + appcc_centro_params,
    )
    sin_verificar = _safe_scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM appcc_registros_plan r
        JOIN appcc_controles_plan c ON c.id = r.control_id
        WHERE r.empresa_id = ?
          AND date(r.fecha) = ?
          AND COALESCE(r.verificado_por, '') = ''
          {appcc_centro_sql}
        """,
        [empresa_id, fecha] + appcc_centro_params,
    )

    incidencias_abiertas = _safe_scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM incidencias
        WHERE empresa_id = ?
          AND COALESCE(requiere_appcc, 0) = 1
          AND COALESCE(estado, 'abierta') NOT IN ('cerrada', 'resuelta', 'cancelada')
          {incidencias_centro_sql}
        """,
        [empresa_id] + incidencias_centro_params,
    )

    temperatura_hoy = 0
    temperatura_fuera = 0
    equipos_temperatura = 0
    equipos_sin_registro = 0
    alertas_temperatura = []
    if _tables_exist(conn, "registros_temperatura", "equipos"):
        temperatura_hoy = _safe_scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM registros_temperatura rt
            JOIN equipos e ON e.id = rt.equipo_id
            WHERE e.empresa_id = ? AND date(rt.fecha) = ?
              {equipos_centro_sql}
            """,
            [empresa_id, fecha] + equipos_centro_params,
        )
        temperatura_fuera = _safe_scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM registros_temperatura rt
            JOIN equipos e ON e.id = rt.equipo_id
            WHERE e.empresa_id = ? AND date(rt.fecha) = ? AND COALESCE(rt.fuera_rango, 0) = 1
              {equipos_centro_sql}
            """,
            [empresa_id, fecha] + equipos_centro_params,
        )
        equipos_temperatura = _safe_scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM equipos e
            WHERE e.empresa_id = ?
              AND COALESCE(e.activo, 1) = 1
              AND (e.rango_min IS NOT NULL OR e.rango_max IS NOT NULL)
              {equipos_centro_sql}
            """,
            [empresa_id] + equipos_centro_params,
        )
        equipos_sin_registro = _safe_scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM equipos e
            WHERE e.empresa_id = ?
              AND COALESCE(e.activo, 1) = 1
              AND (e.rango_min IS NOT NULL OR e.rango_max IS NOT NULL)
              {equipos_centro_sql}
              AND NOT EXISTS (
                  SELECT 1
                  FROM registros_temperatura rt
                  WHERE rt.equipo_id = e.id AND date(rt.fecha) = ?
              )
            """,
            [empresa_id] + equipos_centro_params + [fecha],
        )
        alertas_temperatura = _safe_rows(
            conn,
            f"""
            SELECT rt.*, e.nombre AS equipo_nombre, e.rango_min, e.rango_max
            FROM registros_temperatura rt
            JOIN equipos e ON rt.equipo_id = e.id
            WHERE rt.fuera_rango = 1
              AND rt.fecha >= datetime('now', '-1 day')
              AND e.empresa_id = ?
              {equipos_centro_sql}
            ORDER BY rt.fecha DESC
            LIMIT 8
            """,
            [empresa_id] + equipos_centro_params,
        )

    limpiezas_hoy = _safe_scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM registros_limpieza rl
        JOIN plan_limpieza pl ON pl.id = rl.plan_id
        WHERE rl.empresa_id = ? AND date(rl.fecha) = ?
          {limpieza_centro_sql}
        """,
        [empresa_id, fecha] + limpieza_centro_params,
    ) if _table_exists(conn, "registros_limpieza") else 0
    descongelados_hoy = _safe_scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM registros_descongelado rd
        JOIN lotes l ON l.id = rd.lote_id
        WHERE rd.empresa_id = ? AND date(rd.fecha_control) = ?
          {lotes_centro_sql}
        """,
        [empresa_id, fecha] + lotes_centro_params,
    ) if _table_exists(conn, "registros_descongelado") else 0
    desinfecciones_hoy = _safe_scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM registros_desinfeccion rd
        JOIN lotes l ON l.id = rd.lote_id
        WHERE rd.empresa_id = ? AND date(rd.fecha) = ?
          {lotes_centro_sql}
        """,
        [empresa_id, fecha] + lotes_centro_params,
    ) if _table_exists(conn, "registros_desinfeccion") else 0
    produccion_appcc_hoy = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM produccion_controles_appcc WHERE empresa_id = ? AND date(actualizado_en) = ?",
        (empresa_id, fecha),
    ) if _table_exists(conn, "produccion_controles_appcc") else 0
    deterioros_hoy = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM registros_deterioro_lotes WHERE empresa_id = ? AND date(fecha) = ?",
        (empresa_id, fecha),
    ) if _table_exists(conn, "registros_deterioro_lotes") else 0
    mermas_hoy = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM mermas WHERE empresa_id = ? AND date(fecha) = ?",
        (empresa_id, fecha),
    ) if _table_exists(conn, "mermas") else 0

    filtro_descongelado = _sql_producto_alerta_descongelado("p")
    alertas_descongelado = []
    descongelados_en_curso = 0
    if _tables_exist(conn, "alertas_programadas", "lotes", "productos"):
        alertas_descongelado = _safe_rows(
            conn,
            f"""
            SELECT a.*, l.codigo_lote, p.nombre AS producto_nombre
            FROM alertas_programadas a
            JOIN lotes l ON a.lote_id = l.id
            JOIN productos p ON l.producto_id = p.id
            WHERE COALESCE(a.enviada, 0) = 0
              AND COALESCE(a.resuelta, 0) = 0
              AND a.tipo = 'descongelado'
              AND a.fecha_alerta <= datetime('now', '+7 days')
              AND l.empresa_id = ?
              {lotes_centro_sql}
              AND {filtro_descongelado}
            ORDER BY a.fecha_alerta
            LIMIT 8
            """,
            [empresa_id] + lotes_centro_params,
        )
    if _tables_exist(conn, "lotes", "productos"):
        descongelados_en_curso = _safe_scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM lotes l
            JOIN productos p ON l.producto_id = p.id
            WHERE l.empresa_id = ?
              AND COALESCE(l.estado_descongelado, '') = 'en_curso'
              {lotes_centro_sql}
              AND {filtro_descongelado}
            """,
            [empresa_id] + lotes_centro_params,
        )

    alertas_caducidad = []
    if _tables_exist(conn, "lotes", "productos"):
        alertas_caducidad = _safe_rows(
            conn,
            f"""
            SELECT l.*, p.nombre AS producto_nombre, p.unidad,
                   julianday(l.fecha_caducidad) - julianday('now') AS dias_restantes
            FROM lotes l
            JOIN productos p ON l.producto_id = p.id
            WHERE l.estado = 'activo'
              AND l.fecha_caducidad IS NOT NULL
              AND julianday(l.fecha_caducidad) - julianday('now') <= 7
              AND l.empresa_id = ?
              {lotes_centro_sql}
            ORDER BY l.fecha_caducidad ASC
            LIMIT 8
            """,
            [empresa_id] + lotes_centro_params,
        )

    recientes = _safe_rows(
        conn,
        f"""
        SELECT r.*, c.codigo, c.area, c.etapa
        FROM appcc_registros_plan r
        JOIN appcc_controles_plan c ON c.id = r.control_id
        WHERE r.empresa_id = ?
          {appcc_centro_sql}
        ORDER BY datetime(r.fecha) DESC
        LIMIT 12
        """,
        [empresa_id] + appcc_centro_params,
    )

    incidencias = _safe_rows(
        conn,
        f"""
        SELECT *
        FROM incidencias
        WHERE empresa_id = ?
          AND COALESCE(requiere_appcc, 0) = 1
          AND COALESCE(estado, 'abierta') NOT IN ('cerrada', 'resuelta', 'cancelada')
          {incidencias_centro_sql}
        ORDER BY datetime(fecha) DESC
        LIMIT 8
        """,
        [empresa_id] + incidencias_centro_params,
    )

    cierre = conn.execute(
        f"""
        SELECT *
        FROM appcc_cierres_diarios
        WHERE empresa_id = ? AND fecha = ?
          {cierre_centro_sql}
        LIMIT 1
        """,
        [empresa_id, fecha] + cierre_centro_params,
    ).fetchone()

    registros_por_codigo = {
        c["codigo"]: _to_int(c["registros_hoy"], 0)
        for c in controles
        if c["codigo"]
    }
    operativas = [
        {"nombre": "Recepcion mercancias", "codigo": "REC-01", "conteo": registros_por_codigo.get("REC-01", 0), "detalle": "Pedidos y albaranes"},
        {"nombre": "Temperaturas frio", "codigo": "FRIO-01", "conteo": max(temperatura_hoy, registros_por_codigo.get("FRIO-01", 0)), "detalle": f"{equipos_sin_registro} equipos sin lectura"},
        {"nombre": "Descongelacion", "codigo": "DES-01", "conteo": max(descongelados_hoy, registros_por_codigo.get("DES-01", 0)), "detalle": f"{descongelados_en_curso} en curso"},
        {"nombre": "Desinfeccion verduras", "codigo": "DESINF-01", "conteo": max(desinfecciones_hoy, registros_por_codigo.get("DESINF-01", 0)), "detalle": "Frutas y verduras"},
        {"nombre": "Produccion", "codigo": "ELAB-01", "conteo": max(produccion_appcc_hoy, registros_por_codigo.get("ELAB-01", 0)), "detalle": "Elaboracion, abatimiento y servicio"},
        {"nombre": "Limpieza", "codigo": "LIMP-01", "conteo": max(limpiezas_hoy, registros_por_codigo.get("LIMP-01", 0)), "detalle": "Zonas, equipos y utensilios"},
        {"nombre": "Mermas / deterioro", "codigo": "MERMA-01", "conteo": max(deterioros_hoy + mermas_hoy, registros_por_codigo.get("MERMA-01", 0)), "detalle": "Producto no apto y sobrantes"},
    ]
    if not usuario_es_global_operacion():
        codigos_permitidos = {
            control["codigo"]
            for control in controles
            if control["codigo"]
        }
        operativas = [
            op for op in operativas
            if op["codigo"] in codigos_permitidos or op["conteo"] > 0
        ]

    return {
        "fecha": fecha,
        "controles": controles,
        "kpis": {
            "registros_hoy": registros_hoy,
            "no_ok_hoy": no_ok_hoy,
            "sin_verificar": sin_verificar,
            "incidencias_abiertas": incidencias_abiertas,
            "temperatura_fuera": temperatura_fuera,
            "equipos_sin_registro": equipos_sin_registro,
            "alertas_descongelado": len(alertas_descongelado),
            "alertas_caducidad": len(alertas_caducidad),
        },
        "operativas": operativas,
        "recientes": recientes,
        "incidencias": incidencias,
        "alertas_temperatura": alertas_temperatura,
        "alertas_descongelado": alertas_descongelado,
        "alertas_caducidad": alertas_caducidad,
        "cierre": cierre,
        "origen_label": _origen_label,
    }


def _sql_producto_alerta_descongelado(alias="p"):
    texto = f"LOWER(COALESCE({alias}.familia, '') || ' ' || COALESCE({alias}.nombre, ''))"
    familia = f"LOWER(COALESCE({alias}.familia, ''))"
    conservacion = f"LOWER(COALESCE({alias}.tipo_conservacion, ''))"
    return f"""
        (
            {conservacion} LIKE '%congel%'
            OR {texto} LIKE '%congel%'
            OR {texto} LIKE '%cong%'
            OR {texto} LIKE '%freezer%'
            OR {texto} LIKE '%uco%'
        )
        AND {familia} NOT IN ('helados', 'bebidas', 'bebidas_agua', 'bebidas_refrescos', 'bebidas_licores', 'bebidas_premium')
        AND {texto} NOT LIKE '%helado%'
        AND {texto} NOT LIKE '%ice cream%'
        AND {texto} NOT LIKE '%polo%'
        AND {texto} NOT LIKE '%sorbete%'
        AND {texto} NOT LIKE '%granizado%'
    """

# ============================================
# DASHBOARD APPCC
# ============================================
@appcc_bp.route('/')
@login_required
def appcc_dashboard():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    ensure_equipos_unificados_schema(conn)
    ensure_appcc_plan_schema(conn)
    _seed_appcc_plan_base(conn, empresa_id)
    conn.commit()
    fecha = request.args.get("fecha") or datetime.now().strftime("%Y-%m-%d")
    panel = _resumen_appcc_diario(conn, empresa_id, fecha)
    conn.close()
    return render_template('appcc/dashboard.html', **panel)

    filtro_descongelado = _sql_producto_alerta_descongelado("p")
    
    # Últimos registros de temperatura
    ultimos_temperaturas = conn.execute("""
        SELECT rt.*, e.nombre as equipo_nombre, e.tipo, e.rango_min, e.rango_max
        FROM registros_temperatura rt
        JOIN equipos e ON rt.equipo_id = e.id
        WHERE e.empresa_id = ?
        ORDER BY rt.fecha DESC
        LIMIT 10
    """, (empresa_id,)).fetchall()
    
    # Últimas limpiezas
    ultimas_limpiezas = conn.execute("""
        SELECT rl.*, pl.area, pl.tarea
        FROM registros_limpieza rl
        JOIN plan_limpieza pl ON rl.plan_id = pl.id
        WHERE pl.empresa_id = ?
        ORDER BY rl.fecha DESC
        LIMIT 10
    """, (empresa_id,)).fetchall()
    
    # Alertas de caducidad (próximos 7 días)
    alertas_caducidad = conn.execute("""
        SELECT l.*, p.nombre as producto_nombre, p.unidad,
               julianday(l.fecha_caducidad) - julianday('now') as dias_restantes
        FROM lotes l
        JOIN productos p ON l.producto_id = p.id
        WHERE l.estado = 'activo' 
          AND l.fecha_caducidad IS NOT NULL
          AND julianday(l.fecha_caducidad) - julianday('now') <= 7
          AND julianday(l.fecha_caducidad) - julianday('now') >= 0
          AND l.empresa_id = ?
        ORDER BY l.fecha_caducidad ASC
    """, (empresa_id,)).fetchall()
    
    # Alertas de descongelado (próximas 24h)
    alertas_descongelado = conn.execute(f"""
        SELECT a.*, l.codigo_lote, p.nombre as producto_nombre
        FROM alertas_programadas a
        JOIN lotes l ON a.lote_id = l.id
        JOIN productos p ON l.producto_id = p.id
        WHERE COALESCE(a.enviada, 0) = 0
          AND COALESCE(a.resuelta, 0) = 0
          AND a.tipo = 'descongelado'
          AND a.fecha_alerta <= datetime('now', '+7 days')
          AND l.empresa_id = ?
          AND {filtro_descongelado}
        ORDER BY a.fecha_alerta
        LIMIT 10
    """, (empresa_id,)).fetchall()
    
    # Alertas de temperatura (últimas 24h fuera de rango)
    alertas_temperatura = conn.execute("""
        SELECT rt.*, e.nombre as equipo_nombre, e.rango_min, e.rango_max
        FROM registros_temperatura rt
        JOIN equipos e ON rt.equipo_id = e.id
        WHERE rt.fuera_rango = 1
          AND rt.fecha >= datetime('now', '-1 day')
          AND e.empresa_id = ?
        ORDER BY rt.fecha DESC
    """, (empresa_id,)).fetchall()
    
    conn.close()
    
    return render_template('appcc/dashboard.html',
                         ultimos_temperaturas=ultimos_temperaturas,
                         ultimas_limpiezas=ultimas_limpiezas,
                         alertas_caducidad=alertas_caducidad,
                         alertas_descongelado=alertas_descongelado,
                         alertas_temperatura=alertas_temperatura)


@appcc_bp.route('/cierre-diario', methods=['GET', 'POST'])
@login_required
def cierre_diario_appcc():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    ensure_equipos_unificados_schema(conn)
    ensure_appcc_plan_schema(conn)
    _seed_appcc_plan_base(conn, empresa_id)
    conn.commit()
    fecha = request.values.get("fecha") or datetime.now().strftime("%Y-%m-%d")

    if request.method == "POST":
        bloqueo = _requiere_gestion_appcc(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_appcc(conn)
        if bloqueo_centro:
            return bloqueo_centro
        responsable = request.form.get("responsable") or session.get('user_nombre', 'admin')
        resultado = request.form.get("resultado") or "ok"
        observaciones = request.form.get("observaciones") or ""
        resumen = request.form.get("resumen") or ""
        existente = conn.execute(
            """
            SELECT id
            FROM appcc_cierres_diarios
            WHERE empresa_id = ? AND fecha = ?
              AND COALESCE(centro_id, 0) = COALESCE(?, 0)
            LIMIT 1
            """,
            (empresa_id, fecha, centro_id),
        ).fetchone()
        if existente:
            conn.execute(
                """
                UPDATE appcc_cierres_diarios
                SET resultado = ?,
                    responsable = ?,
                    observaciones = ?,
                    resumen = ?,
                    centro_id = ?,
                    actualizado_en = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (resultado, responsable, observaciones, resumen, centro_id, existente["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO appcc_cierres_diarios
                (empresa_id, centro_id, fecha, resultado, responsable, observaciones, resumen)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (empresa_id, centro_id, fecha, resultado, responsable, observaciones, resumen),
            )
        conn.commit()
        conn.close()
        flash("Cierre diario APPCC guardado.", "success")
        return redirect(url_for("appcc.appcc_dashboard", fecha=fecha))

    panel = _resumen_appcc_diario(conn, empresa_id, fecha)
    conn.close()
    return render_template("appcc/cierre_diario.html", **panel)


@appcc_bp.route('/descongelados')
@login_required
def alertas_descongelados_appcc():
    if _rol_actual_key() not in ROLES_OPERATIVA_COCINA:
        flash("No tienes permisos para la operativa de congelados de produccion.", "warning")
        return redirect(url_for("home"))
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    filtro_descongelado = _sql_producto_alerta_descongelado("p")
    lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, empresa_id, "l")

    alertas = conn.execute(f"""
        SELECT a.*, l.codigo_lote, l.cantidad_actual, l.estado_descongelado,
               p.nombre as producto_nombre, p.unidad,
               pr.nombre as proveedor_nombre,
               (julianday(a.fecha_alerta) - julianday('now')) * 24 as horas_restantes
        FROM alertas_programadas a
        JOIN lotes l ON a.lote_id = l.id
        JOIN productos p ON l.producto_id = p.id
        LEFT JOIN proveedores pr ON l.proveedor_id = pr.id
        WHERE COALESCE(a.enviada, 0) = 0
          AND COALESCE(a.resuelta, 0) = 0
          AND a.tipo = 'descongelado'
          AND a.fecha_alerta <= datetime('now', '+7 days')
          AND l.empresa_id = ?
          {lotes_centro_sql}
          AND {filtro_descongelado}
        ORDER BY a.fecha_alerta
    """, [empresa_id] + lotes_centro_params).fetchall()

    pendientes = conn.execute(f"""
        SELECT l.*, p.nombre as producto_nombre, p.unidad,
               COALESCE(NULLIF(p.tiempo_descongelado_horas, 0), 72) AS tiempo_descongelado_horas
        FROM lotes l
        JOIN productos p ON l.producto_id = p.id
        WHERE l.estado = 'activo'
          AND l.fecha_descongelado IS NULL
          AND l.empresa_id = ?
          {lotes_centro_sql}
          AND {filtro_descongelado}
        ORDER BY l.fecha_caducidad IS NULL, l.fecha_caducidad, p.nombre
    """, [empresa_id] + lotes_centro_params).fetchall()
    conn.close()

    return render_template(
        'lotes/alertas.html',
        alertas=alertas,
        pendientes=pendientes,
        appcc_view=True,
    )

# ============================================
# PLAN APPCC
# ============================================
@appcc_bp.route('/plan')
@login_required
def plan_appcc():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    ensure_appcc_plan_schema(conn)
    conn.commit()
    appcc_centro_sql, appcc_centro_params = _filtro_appcc_centro(conn, empresa_id, "c")
    incidencias_centro_sql, incidencias_centro_params = _filtro_incidencias_centro(conn, empresa_id, "i")

    controles = conn.execute(f"""
        SELECT c.*,
               COUNT(r.id) AS total_registros,
               MAX(r.fecha) AS ultimo_registro,
               SUM(CASE WHEN COALESCE(r.resultado, 'ok') != 'ok' THEN 1 ELSE 0 END) AS no_ok
        FROM appcc_controles_plan c
        LEFT JOIN appcc_registros_plan r ON r.control_id = c.id AND r.empresa_id = c.empresa_id
        WHERE c.empresa_id = ? AND COALESCE(c.activo, 1) = 1
          {appcc_centro_sql}
        GROUP BY c.id
        ORDER BY c.area, c.etapa, c.codigo
    """, [empresa_id] + appcc_centro_params).fetchall()

    ultimos = conn.execute(f"""
        SELECT r.*, c.codigo, c.area, c.etapa, c.tipo_control
        FROM appcc_registros_plan r
        JOIN appcc_controles_plan c ON c.id = r.control_id
        WHERE r.empresa_id = ?
          {appcc_centro_sql}
        ORDER BY datetime(r.fecha) DESC
        LIMIT 20
    """, [empresa_id] + appcc_centro_params).fetchall()

    no_conformidades = conn.execute(f"""
        SELECT i.*
        FROM incidencias i
        WHERE i.empresa_id = ?
          AND COALESCE(i.requiere_appcc, 0) = 1
          AND COALESCE(i.estado, 'abierta') NOT IN ('cerrada', 'resuelta', 'cancelada')
          {incidencias_centro_sql}
        ORDER BY datetime(i.fecha) DESC
        LIMIT 30
    """, [empresa_id] + incidencias_centro_params).fetchall()

    resumen = {
        "controles": len(controles),
        "registros": sum(_to_int(c["total_registros"], 0) for c in controles),
        "no_ok": sum(_to_int(c["no_ok"], 0) for c in controles),
        "no_conformidades": len(no_conformidades),
    }
    conn.close()
    return render_template(
        'appcc/plan.html',
        controles=controles,
        ultimos=ultimos,
        no_conformidades=no_conformidades,
        resumen=resumen,
    )


@appcc_bp.route('/operativa')
@login_required
def operativa_appcc():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    ensure_appcc_plan_schema(conn)
    conn.commit()

    fecha_desde = request.args.get('desde') or (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    fecha_hasta = request.args.get('hasta') or datetime.now().strftime('%Y-%m-%d')
    origen = (request.args.get('origen') or '').strip()
    resultado = (request.args.get('resultado') or '').strip()
    codigo = (request.args.get('codigo') or '').strip()
    q = (request.args.get('q') or '').strip()

    where = ["r.empresa_id = ?"]
    params = [empresa_id]
    appcc_centro_sql, appcc_centro_params = _filtro_appcc_centro(conn, empresa_id, "c")
    if appcc_centro_sql:
        where.append(appcc_centro_sql.replace(" AND ", "", 1))
        params.extend(appcc_centro_params)
    if fecha_desde:
        where.append("date(r.fecha) >= ?")
        params.append(fecha_desde)
    if fecha_hasta:
        where.append("date(r.fecha) <= ?")
        params.append(fecha_hasta)
    if origen:
        if origen == "manual":
            where.append("(r.origen_tabla IS NULL OR r.origen_tabla = '')")
        else:
            where.append("r.origen_tabla = ?")
            params.append(origen)
    if resultado:
        where.append("COALESCE(r.resultado, 'ok') = ?")
        params.append(resultado)
    if codigo:
        where.append("c.codigo = ?")
        params.append(codigo)
    if q:
        like = f"%{q}%"
        where.append("""
            (
                c.codigo LIKE ?
                OR c.area LIKE ?
                OR c.etapa LIKE ?
                OR r.valor LIKE ?
                OR r.observaciones LIKE ?
                OR r.accion_correctiva LIKE ?
                OR i.descripcion LIKE ?
            )
        """)
        params.extend([like, like, like, like, like, like, like])

    where_sql = " AND ".join(where)
    registros = conn.execute(f"""
        SELECT r.*,
               c.codigo,
               c.area,
               c.etapa,
               c.tipo_control,
               c.limite_critico,
               i.descripcion AS incidencia_descripcion,
               i.estado AS incidencia_estado,
               i.gravedad AS incidencia_gravedad
        FROM appcc_registros_plan r
        JOIN appcc_controles_plan c ON c.id = r.control_id
        LEFT JOIN incidencias i ON i.id = r.incidencia_id
        WHERE {where_sql}
        ORDER BY datetime(r.fecha) DESC
        LIMIT 300
    """, params).fetchall()

    resumen = conn.execute(f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN COALESCE(r.resultado, 'ok') = 'ok' THEN 1 ELSE 0 END) AS ok,
            SUM(CASE WHEN COALESCE(r.resultado, 'ok') = 'no_ok' THEN 1 ELSE 0 END) AS no_ok,
            SUM(CASE WHEN COALESCE(r.resultado, 'ok') = 'pendiente' THEN 1 ELSE 0 END) AS pendientes,
            SUM(CASE WHEN r.incidencia_id IS NOT NULL THEN 1 ELSE 0 END) AS incidencias,
            SUM(CASE WHEN r.verificado_por IS NOT NULL AND r.verificado_por != '' THEN 1 ELSE 0 END) AS verificados
        FROM appcc_registros_plan r
        JOIN appcc_controles_plan c ON c.id = r.control_id
        LEFT JOIN incidencias i ON i.id = r.incidencia_id
        WHERE {where_sql}
    """, params).fetchone()

    resumen_origen = conn.execute(f"""
        SELECT COALESCE(NULLIF(r.origen_tabla, ''), 'manual') AS origen,
               COUNT(*) AS total,
               SUM(CASE WHEN COALESCE(r.resultado, 'ok') != 'ok' THEN 1 ELSE 0 END) AS no_ok
        FROM appcc_registros_plan r
        JOIN appcc_controles_plan c ON c.id = r.control_id
        LEFT JOIN incidencias i ON i.id = r.incidencia_id
        WHERE {where_sql}
        GROUP BY COALESCE(NULLIF(r.origen_tabla, ''), 'manual')
        ORDER BY total DESC
    """, params).fetchall()

    origenes = conn.execute(f"""
        SELECT DISTINCT COALESCE(NULLIF(origen_tabla, ''), 'manual') AS origen
        FROM appcc_registros_plan r
        JOIN appcc_controles_plan c ON c.id = r.control_id
        WHERE r.empresa_id = ?
          {appcc_centro_sql}
        ORDER BY origen
    """, [empresa_id] + appcc_centro_params).fetchall()
    controles = conn.execute(f"""
        SELECT DISTINCT codigo, area, etapa
        FROM appcc_controles_plan c
        WHERE c.empresa_id = ? AND COALESCE(c.activo, 1) = 1
          {appcc_centro_sql}
        ORDER BY codigo
    """, [empresa_id] + appcc_centro_params).fetchall()

    filtros = {
        "desde": fecha_desde,
        "hasta": fecha_hasta,
        "origen": origen,
        "resultado": resultado,
        "codigo": codigo,
        "q": q,
    }
    conn.close()
    return render_template(
        'appcc/operativa.html',
        registros=registros,
        resumen=resumen,
        resumen_origen=resumen_origen,
        origenes=origenes,
        controles=controles,
        filtros=filtros,
        origen_label=_origen_label,
    )


@appcc_bp.route('/plan/base', methods=['POST'])
@login_required
def crear_plan_appcc_base():
    conn = get_db()
    bloqueo = _requiere_gestion_appcc(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_appcc(conn)
    if bloqueo_centro:
        return bloqueo_centro
    empresa_id = session.get('user_empresa_id', 1)
    creados = _seed_appcc_plan_base(conn, empresa_id)
    conn.commit()
    conn.close()
    if creados:
        flash(f'Plan APPCC base creado: {creados} controles.', 'success')
    else:
        flash('El plan APPCC base ya estaba creado.', 'info')
    return redirect(url_for('appcc.plan_appcc'))


@appcc_bp.route('/plan/controles/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_control_appcc():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    departamento_id = None if usuario_es_global_operacion() else usuario_departamento_id()
    subdepartamento_id = None if usuario_es_global_operacion() else usuario_subdepartamento_id()
    ensure_appcc_plan_schema(conn)
    if request.method == 'POST':
        bloqueo = _requiere_gestion_appcc(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_appcc(conn)
        if bloqueo_centro:
            return bloqueo_centro
        codigo = request.form.get('codigo')
        if departamento_id and codigo:
            codigo = f"{departamento_id}-{codigo}"
        conn.execute("""
            INSERT INTO appcc_controles_plan
            (empresa_id, centro_id, departamento_id, subdepartamento_id, codigo, area, etapa, tipo_control, peligro, medida_preventiva,
             limite_critico, frecuencia, responsable, accion_correctiva, verificacion,
             modulo_origen, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            empresa_id,
            centro_id,
            departamento_id,
            subdepartamento_id,
            codigo,
            request.form.get('area') or 'APPCC',
            request.form.get('etapa'),
            request.form.get('tipo_control') or 'prerrequisito',
            request.form.get('peligro'),
            request.form.get('medida_preventiva'),
            request.form.get('limite_critico'),
            request.form.get('frecuencia'),
            request.form.get('responsable'),
            request.form.get('accion_correctiva'),
            request.form.get('verificacion'),
            request.form.get('modulo_origen'),
        ))
        conn.commit()
        conn.close()
        flash('Control APPCC creado.', 'success')
        return redirect(url_for('appcc.plan_appcc'))
    conn.close()
    return render_template('appcc/control_form.html', control=None)


@appcc_bp.route('/plan/controles/<int:control_id>/registrar', methods=['GET', 'POST'])
@login_required
def registrar_control_appcc(control_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    ensure_appcc_plan_schema(conn)
    appcc_centro_sql, appcc_centro_params = _filtro_appcc_centro(conn, empresa_id, "appcc_controles_plan")
    control = conn.execute(
        f"SELECT * FROM appcc_controles_plan WHERE id = ? AND empresa_id = ? {appcc_centro_sql}",
        [control_id, empresa_id] + appcc_centro_params,
    ).fetchone()
    if not control:
        conn.close()
        flash('Control APPCC no encontrado.', 'danger')
        return redirect(url_for('appcc.plan_appcc'))

    if request.method == 'POST':
        bloqueo = _requiere_gestion_appcc(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_appcc(conn)
        if bloqueo_centro:
            return bloqueo_centro
        resultado = request.form.get('resultado') or 'ok'
        valor = request.form.get('valor')
        observaciones = request.form.get('observaciones')
        accion_correctiva = request.form.get('accion_correctiva')
        usuario = session.get('user_nombre', 'admin')
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO appcc_registros_plan
            (empresa_id, control_id, valor, resultado, realizado_por, observaciones, accion_correctiva)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (empresa_id, control_id, valor, resultado, usuario, observaciones, accion_correctiva))
        registro_id = cursor.lastrowid

        if resultado != 'ok':
            descripcion = (
                f"No conformidad APPCC {control['codigo'] or control['area']}: "
                f"{control['etapa'] or ''}. Valor: {valor or '-'}"
            )
            cursor.execute("""
                INSERT INTO incidencias
                (tipo, referencia_id, usuario, descripcion, gravedad, estado, origen_modulo,
                 destino_modulo, categoria_operativa, requiere_appcc, origen_tabla, origen_id, empresa_id)
                VALUES (?, ?, ?, ?, ?, 'abierta', 'appcc', 'appcc', ?, 1, 'appcc_registros_plan', ?, ?)
            """, (
                'appcc_control',
                registro_id,
                usuario,
                descripcion,
                'alta' if control['tipo_control'] == 'pcc' else 'media',
                control['tipo_control'] or 'appcc',
                registro_id,
                empresa_id,
            ))
            incidencia_id = cursor.lastrowid
            cursor.execute(
                "UPDATE appcc_registros_plan SET incidencia_id = ? WHERE id = ? AND empresa_id = ?",
                (incidencia_id, registro_id, empresa_id),
            )

        conn.commit()
        conn.close()
        if resultado == 'ok':
            flash('Registro APPCC guardado.', 'success')
        else:
            flash('Registro guardado y no conformidad APPCC abierta.', 'warning')
        return redirect(url_for('appcc.plan_appcc'))

    conn.close()
    return render_template('appcc/registro_form.html', control=control)


@appcc_bp.route('/plan/registros/<int:registro_id>/verificar', methods=['POST'])
@login_required
def verificar_registro_appcc(registro_id):
    conn = get_db()
    bloqueo = _requiere_gestion_appcc(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_appcc(conn)
    if bloqueo_centro:
        return bloqueo_centro
    empresa_id = session.get('user_empresa_id', 1)
    ensure_appcc_plan_schema(conn)
    appcc_centro_sql, appcc_centro_params = _filtro_appcc_centro(conn, empresa_id, "c")
    conn.execute(f"""
        UPDATE appcc_registros_plan
        SET verificado_por = ?, fecha_verificacion = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
          AND control_id IN (
              SELECT c.id FROM appcc_controles_plan c
              WHERE c.empresa_id = ? {appcc_centro_sql}
          )
    """, [session.get('user_nombre', 'admin'), registro_id, empresa_id, empresa_id] + appcc_centro_params)
    conn.commit()
    conn.close()
    flash('Registro APPCC verificado.', 'success')
    siguiente = request.form.get('next') or ''
    if siguiente and not siguiente.startswith(('http://', 'https://', '//')):
        return redirect(siguiente)
    return redirect(url_for('appcc.plan_appcc'))


# ============================================
# EQUIPOS APPCC
# ============================================
@appcc_bp.route('/equipos')
@login_required
def lista_equipos():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    ensure_equipos_unificados_schema(conn)
    conn.commit()
    equipos_centro_sql, equipos_centro_params = _filtro_equipos_centro(conn, empresa_id, "e")
    
    equipos = conn.execute(f"""
        SELECT e.*, 
               d.nombre as departamento_nombre,
               s.nombre as subdepartamento_nombre,
               pv.nombre as punto_venta_nombre,
               em.id as equipo_mantenimiento_id,
               em.codigo as codigo_mantenimiento,
               a.id as almacen_id,
               a.nombre as almacen_nombre
        FROM equipos e
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN subdepartamentos s ON e.subdepartamento_id = s.id
        LEFT JOIN puntos_venta pv ON e.punto_venta_id = pv.id
        LEFT JOIN equipos_mantenimiento em ON em.id = e.equipo_mantenimiento_id
        LEFT JOIN almacenes a ON a.id = e.almacen_id
        WHERE e.activo = 1 AND e.empresa_id = ?
          {equipos_centro_sql}
        ORDER BY d.nombre, s.nombre, e.nombre
    """, [empresa_id] + equipos_centro_params).fetchall()
    
    conn.close()
    return render_template('appcc/equipos.html', equipos=equipos)

@appcc_bp.route('/equipos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_equipo():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    ensure_equipos_unificados_schema(conn)
    conn.commit()
    
    if request.method == "POST":
        bloqueo = _requiere_gestion_appcc(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_appcc(conn)
        if bloqueo_centro:
            return bloqueo_centro
        nombre = request.form["nombre"]
        tipo = request.form["tipo"]
        ubicacion = request.form.get("ubicacion", "")
        departamento_id = request.form.get("departamento_id")
        subdepartamento_id = request.form.get("subdepartamento_id")
        punto_venta_id = request.form.get("punto_venta_id")
        rango_min = request.form.get("rango_min")
        rango_max = request.form.get("rango_max")
        responsable = request.form.get("responsable", "")
        centro_equipo_id = asignar_centro_desde_departamento(conn, departamento_id, centro_id)
        
        conn.execute("""
            INSERT INTO equipos 
            (nombre, tipo, ubicacion, departamento_id, subdepartamento_id, punto_venta_id, 
             rango_min, rango_max, responsable, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nombre, tipo, ubicacion, departamento_id, subdepartamento_id, punto_venta_id,
              rango_min, rango_max, responsable, empresa_id, centro_equipo_id))
        ensure_equipos_unificados_schema(conn)
        conn.commit()
        conn.close()
        
        flash('✅ Equipo añadido', 'success')
        return redirect(url_for('appcc.lista_equipos'))
    
    centro_sql, centro_params = _centro_sql_simple(conn, "departamentos", "departamentos")
    departamentos = conn.execute("SELECT * FROM departamentos WHERE activo = 1 AND empresa_id = ?" + centro_sql, [empresa_id] + centro_params).fetchall()
    centro_sql, centro_params = _centro_sql_simple(conn, "subdepartamentos", "subdepartamentos")
    subdepartamentos = conn.execute("SELECT * FROM subdepartamentos WHERE activo = 1 AND empresa_id = ?" + centro_sql, [empresa_id] + centro_params).fetchall()
    centro_sql, centro_params = _centro_sql_simple(conn, "puntos_venta", "puntos_venta")
    puntos_venta = conn.execute("SELECT * FROM puntos_venta WHERE activo = 1 AND empresa_id = ?" + centro_sql, [empresa_id] + centro_params).fetchall()
    
    conn.close()
    return render_template('appcc/nuevo_equipo.html', 
                         departamentos=departamentos,
                         subdepartamentos=subdepartamentos,
                         puntos_venta=puntos_venta)

@appcc_bp.route('/equipos/<int:equipo_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_equipo(equipo_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    ensure_equipos_unificados_schema(conn)
    conn.commit()
    
    if request.method == "POST":
        bloqueo = _requiere_gestion_appcc(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_appcc(conn)
        if bloqueo_centro:
            return bloqueo_centro
        equipos_centro_sql, equipos_centro_params = _filtro_equipos_centro(conn, empresa_id, "equipos")
        equipo_existente = conn.execute(
            f"SELECT id FROM equipos WHERE id = ? AND empresa_id = ? {equipos_centro_sql}",
            [equipo_id, empresa_id] + equipos_centro_params,
        ).fetchone()
        if not equipo_existente:
            conn.close()
            flash('Equipo no encontrado en el centro activo.', 'warning')
            return redirect(url_for('appcc.lista_equipos'))
        nombre = request.form["nombre"]
        tipo = request.form["tipo"]
        ubicacion = request.form.get("ubicacion", "")
        departamento_id = request.form.get("departamento_id")
        subdepartamento_id = request.form.get("subdepartamento_id")
        punto_venta_id = request.form.get("punto_venta_id")
        rango_min = request.form.get("rango_min")
        rango_max = request.form.get("rango_max")
        responsable = request.form.get("responsable", "")
        activo = 1 if request.form.get("activo") else 0
        centro_equipo_id = asignar_centro_desde_departamento(conn, departamento_id, centro_id)
        
        conn.execute("""
            UPDATE equipos 
            SET nombre=?, tipo=?, ubicacion=?, departamento_id=?, subdepartamento_id=?,
                punto_venta_id=?, rango_min=?, rango_max=?, responsable=?, activo=?, centro_id=?
            WHERE id=? AND empresa_id=?
        """, (nombre, tipo, ubicacion, departamento_id, subdepartamento_id, punto_venta_id,
              rango_min, rango_max, responsable, activo, centro_equipo_id, equipo_id, empresa_id))
        ensure_equipos_unificados_schema(conn)
        conn.commit()
        conn.close()
        
        flash('✅ Equipo actualizado', 'success')
        return redirect(url_for('appcc.lista_equipos'))
    
    equipos_centro_sql, equipos_centro_params = _filtro_equipos_centro(conn, empresa_id, "equipos")
    equipo = conn.execute(
        f"SELECT * FROM equipos WHERE id = ? AND empresa_id = ? {equipos_centro_sql}",
        [equipo_id, empresa_id] + equipos_centro_params,
    ).fetchone()
    if not equipo:
        conn.close()
        flash('Equipo no encontrado en el centro activo.', 'warning')
        return redirect(url_for('appcc.lista_equipos'))
    
    if not equipo:
        conn.close()
        flash('❌ Equipo no encontrado', 'danger')
        return redirect(url_for('appcc.lista_equipos'))
    
    centro_sql, centro_params = _centro_sql_simple(conn, "departamentos", "departamentos")
    departamentos = conn.execute("SELECT * FROM departamentos WHERE activo = 1 AND empresa_id = ?" + centro_sql, [empresa_id] + centro_params).fetchall()
    centro_sql, centro_params = _centro_sql_simple(conn, "subdepartamentos", "subdepartamentos")
    subdepartamentos = conn.execute("SELECT * FROM subdepartamentos WHERE activo = 1 AND empresa_id = ?" + centro_sql, [empresa_id] + centro_params).fetchall()
    centro_sql, centro_params = _centro_sql_simple(conn, "puntos_venta", "puntos_venta")
    puntos_venta = conn.execute("SELECT * FROM puntos_venta WHERE activo = 1 AND empresa_id = ?" + centro_sql, [empresa_id] + centro_params).fetchall()
    conn.close()
    
    return render_template('appcc/editar_equipo.html', 
                         equipo=equipo,
                         departamentos=departamentos,
                         subdepartamentos=subdepartamentos,
                         puntos_venta=puntos_venta)

@appcc_bp.route('/equipos/<int:equipo_id>/registrar', methods=['GET', 'POST'])
@login_required
def registrar_temperatura(equipo_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    ensure_equipos_unificados_schema(conn)
    ensure_incidencias_context_schema(conn)
    conn.commit()
    
    equipos_centro_sql, equipos_centro_params = _filtro_equipos_centro(conn, empresa_id, "equipos")
    equipo = conn.execute(
        f"SELECT * FROM equipos WHERE id = ? AND empresa_id = ? {equipos_centro_sql}",
        [equipo_id, empresa_id] + equipos_centro_params,
    ).fetchone()
    if not equipo:
        conn.close()
        flash('Equipo no encontrado en el centro activo.', 'warning')
        return redirect(url_for('appcc.lista_equipos'))
    
    if request.method == "POST":
        bloqueo = _requiere_gestion_appcc(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_appcc(conn)
        if bloqueo_centro:
            return bloqueo_centro
        temperatura = float(request.form["temperatura"])
        observaciones = request.form.get("observaciones", "")
        
        fuera_rango = 0
        if equipo['rango_min'] and equipo['rango_max']:
            if temperatura < equipo['rango_min'] or temperatura > equipo['rango_max']:
                fuera_rango = 1
        
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO registros_temperatura (
                equipo_id, temperatura, usuario, observaciones, fuera_rango,
                equipo_mantenimiento_id, almacen_id, empresa_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            equipo_id,
            temperatura,
            session.get('user_nombre', 'admin'),
            observaciones,
            fuera_rango,
            equipo['equipo_mantenimiento_id'],
            equipo['almacen_id'],
            empresa_id,
        ))
        
        registro_id = cursor.lastrowid
        
        if fuera_rango:
            cursor.execute("""
                INSERT INTO incidencias (tipo, referencia_id, usuario, descripcion, gravedad, estado, empresa_id)
                VALUES (?, ?, ?, ?, ?, 'abierta', ?)
            """, ('temperatura', registro_id, session.get('user_nombre', 'admin'),
                  f"Temperatura fuera de rango en {equipo['nombre']}: {temperatura}°C", 'alta', empresa_id))
            
            incidencia_id = cursor.lastrowid
            cursor.execute("""
                UPDATE incidencias
                SET origen_modulo = 'appcc',
                    destino_modulo = 'appcc',
                    categoria_operativa = 'temperatura',
                    departamento_id = ?,
                    subdepartamento_id = ?,
                    punto_venta_id = ?,
                    ubicacion = ?,
                    equipo_appcc_id = ?,
                    equipo_mantenimiento_id = ?,
                    requiere_appcc = 1,
                    requiere_mantenimiento = 0
                WHERE id = ?
            """, (
                equipo['departamento_id'],
                equipo['subdepartamento_id'],
                equipo['punto_venta_id'],
                equipo['ubicacion'],
                equipo_id,
                equipo['equipo_mantenimiento_id'],
                incidencia_id,
            ))
            cursor.execute("UPDATE registros_temperatura SET incidencia_id = ? WHERE id = ?", 
                         (incidencia_id, registro_id))

        _registrar_operacion_appcc(
            conn,
            empresa_id,
            "FRIO-01",
            valor=f"{temperatura} C",
            resultado="no_ok" if fuera_rango else "ok",
            usuario=session.get('user_nombre', 'admin'),
            observaciones=observaciones or f"Equipo: {equipo['nombre']}",
            accion_correctiva="Revisar equipo y mover producto si procede" if fuera_rango else "",
            origen_tabla="registros_temperatura",
            origen_id=registro_id,
            codigo_operacion=f"temperatura-{registro_id}",
            incidencia_id=incidencia_id if fuera_rango else None,
        )
        
        conn.commit()
        conn.close()
        
        if fuera_rango:
            flash(f'⚠️ ALERTA: Temperatura fuera de rango ({temperatura}°C)', 'warning')
        else:
            flash('✅ Temperatura registrada', 'success')
        
        return redirect(url_for('appcc.lista_equipos'))
    
    conn.close()
    return render_template('appcc/registrar_temperatura.html', equipo=equipo)

@appcc_bp.route('/equipos/<int:equipo_id>/historial')
@login_required
def historial_temperaturas(equipo_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    ensure_equipos_unificados_schema(conn)
    conn.commit()
    
    equipos_centro_sql, equipos_centro_params = _filtro_equipos_centro(conn, empresa_id, "equipos")
    equipo = conn.execute(
        f"SELECT * FROM equipos WHERE id = ? AND empresa_id = ? {equipos_centro_sql}",
        [equipo_id, empresa_id] + equipos_centro_params,
    ).fetchone()
    if not equipo:
        conn.close()
        flash('Equipo no encontrado en el centro activo.', 'warning')
        return redirect(url_for('appcc.lista_equipos'))
    
    registros = conn.execute("""
        SELECT rt.*, i.descripcion as incidencia_desc
        FROM registros_temperatura rt
        LEFT JOIN incidencias i ON rt.incidencia_id = i.id
        WHERE rt.equipo_id = ? AND rt.empresa_id = ?
        ORDER BY rt.fecha DESC
        LIMIT 100
    """, (equipo_id, empresa_id)).fetchall()
    
    conn.close()
    return render_template('appcc/historial_temperaturas.html', equipo=equipo, registros=registros)

# ============================================
# LIMPIEZA
# ============================================
@appcc_bp.route('/limpieza')
@login_required
def plan_limpieza():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    limpieza_centro_sql, limpieza_centro_params = _filtro_plan_limpieza_centro(conn, empresa_id, "pl")
    
    planes = conn.execute(f"""
        SELECT pl.*, 
               d.nombre as departamento_nombre,
               s.nombre as subdepartamento_nombre,
               pv.nombre as punto_venta_nombre
        FROM plan_limpieza pl
        LEFT JOIN departamentos d ON pl.departamento_id = d.id
        LEFT JOIN subdepartamentos s ON pl.subdepartamento_id = s.id
        LEFT JOIN puntos_venta pv ON pl.punto_venta_id = pv.id
        WHERE pl.activo = 1 AND pl.empresa_id = ?
          {limpieza_centro_sql}
        ORDER BY pl.frecuencia, pl.area
    """, [empresa_id] + limpieza_centro_params).fetchall()
    
    ultimos = conn.execute(f"""
        SELECT rl.*, pl.area, pl.tarea
        FROM registros_limpieza rl
        JOIN plan_limpieza pl ON rl.plan_id = pl.id
        WHERE pl.empresa_id = ?
          {limpieza_centro_sql}
        ORDER BY rl.fecha DESC
        LIMIT 50
    """, [empresa_id] + limpieza_centro_params).fetchall()
    
    conn.close()
    return render_template('appcc/limpieza.html', planes=planes, ultimos=ultimos)

@appcc_bp.route('/limpieza/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_plan_limpieza():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    
    if request.method == "POST":
        bloqueo = _requiere_gestion_appcc(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_appcc(conn)
        if bloqueo_centro:
            return bloqueo_centro
        area = request.form["area"]
        tarea = request.form["tarea"]
        frecuencia = request.form["frecuencia"]
        departamento_id = request.form.get("departamento_id")
        subdepartamento_id = request.form.get("subdepartamento_id")
        punto_venta_id = request.form.get("punto_venta_id")
        responsable = request.form.get("responsable", "")
        productos = request.form.get("productos", "")
        instrucciones = request.form.get("instrucciones", "")
        centro_plan_id = asignar_centro_desde_departamento(conn, departamento_id, centro_id)
        
        conn.execute("""
            INSERT INTO plan_limpieza 
            (area, tarea, frecuencia, departamento_id, subdepartamento_id, punto_venta_id, 
             responsable, productos, instrucciones, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (area, tarea, frecuencia, departamento_id, subdepartamento_id, punto_venta_id,
              responsable, productos, instrucciones, empresa_id, centro_plan_id))
        conn.commit()
        conn.close()
        
        flash('✅ Plan de limpieza añadido', 'success')
        return redirect(url_for('appcc.plan_limpieza'))
    
    centro_sql, centro_params = _centro_sql_simple(conn, "departamentos", "departamentos")
    departamentos = conn.execute("SELECT * FROM departamentos WHERE activo = 1 AND empresa_id = ?" + centro_sql, [empresa_id] + centro_params).fetchall()
    centro_sql, centro_params = _centro_sql_simple(conn, "subdepartamentos", "subdepartamentos")
    subdepartamentos = conn.execute("SELECT * FROM subdepartamentos WHERE activo = 1 AND empresa_id = ?" + centro_sql, [empresa_id] + centro_params).fetchall()
    centro_sql, centro_params = _centro_sql_simple(conn, "puntos_venta", "puntos_venta")
    puntos_venta = conn.execute("SELECT * FROM puntos_venta WHERE activo = 1 AND empresa_id = ?" + centro_sql, [empresa_id] + centro_params).fetchall()
    
    conn.close()
    return render_template('appcc/nuevo_plan_limpieza.html',
                         departamentos=departamentos,
                         subdepartamentos=subdepartamentos,
                         puntos_venta=puntos_venta)

@appcc_bp.route('/limpieza/registrar/<int:plan_id>', methods=['POST'])
@login_required
def registrar_limpieza(plan_id):
    observaciones = request.form.get("observaciones", "")
    empresa_id = session.get('user_empresa_id', 1)
    
    conn = get_db()
    bloqueo = _requiere_gestion_appcc(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_appcc(conn)
    if bloqueo_centro:
        return bloqueo_centro
    limpieza_centro_sql, limpieza_centro_params = _filtro_plan_limpieza_centro(conn, empresa_id, "pl")
    plan = conn.execute(
        f"SELECT * FROM plan_limpieza pl WHERE pl.id = ? AND pl.empresa_id = ? {limpieza_centro_sql}",
        [plan_id, empresa_id] + limpieza_centro_params,
    ).fetchone()
    if not plan:
        conn.close()
        flash('Plan de limpieza no encontrado en el centro activo.', 'warning')
        return redirect(url_for('appcc.plan_limpieza'))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO registros_limpieza (plan_id, realizada_por, observaciones, empresa_id)
        VALUES (?, ?, ?, ?)
    """, (plan_id, session.get('user_nombre', 'admin'), observaciones, empresa_id))
    registro_id = cursor.lastrowid

    valor = "Limpieza registrada"
    if plan:
        valor = f"{plan['area'] or '-'} / {plan['tarea'] or '-'}"
    _registrar_operacion_appcc(
        conn,
        empresa_id,
        "LIMP-01",
        valor=valor,
        resultado="ok",
        usuario=session.get('user_nombre', 'admin'),
        observaciones=observaciones,
        origen_tabla="registros_limpieza",
        origen_id=registro_id,
        codigo_operacion=f"limpieza-{registro_id}",
    )
    
    conn.commit()
    conn.close()
    
    flash('✅ Limpieza registrada', 'success')
    return redirect(url_for('appcc.plan_limpieza'))

# ============================================
# CADUCIDADES
# ============================================
@appcc_bp.route('/caducidades')
@login_required
def control_caducidades():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    ensure_alertas_caducidad_schema(conn)
    conn.commit()
    lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, empresa_id, "l")
    
    # Próximos a caducar (7 días)
    proximos = conn.execute(f"""
        SELECT l.*, p.nombre as producto_nombre, p.unidad,
               julianday(l.fecha_caducidad) - julianday('now') as dias_restantes,
               a.nombre as almacen_nombre
        FROM lotes l
        JOIN productos p ON l.producto_id = p.id
        LEFT JOIN almacenes a ON l.almacen_id = a.id
        WHERE l.estado = 'activo'
          AND l.fecha_caducidad IS NOT NULL
          AND julianday(l.fecha_caducidad) - julianday('now') <= 7
          AND julianday(l.fecha_caducidad) - julianday('now') >= 0
          AND l.empresa_id = ?
          {lotes_centro_sql}
        ORDER BY l.fecha_caducidad ASC
    """, [empresa_id] + lotes_centro_params).fetchall()
    
    # Caducados
    caducados = conn.execute(f"""
        SELECT l.*, p.nombre as producto_nombre, p.unidad,
               a.nombre as almacen_nombre
        FROM lotes l
        JOIN productos p ON l.producto_id = p.id
        LEFT JOIN almacenes a ON l.almacen_id = a.id
        WHERE l.estado = 'activo'
          AND l.fecha_caducidad IS NOT NULL
          AND julianday(l.fecha_caducidad) - julianday('now') < 0
          AND l.empresa_id = ?
          {lotes_centro_sql}
        ORDER BY l.fecha_caducidad ASC
    """, [empresa_id] + lotes_centro_params).fetchall()
    
    # Obtener alertas no resueltas
    alertas = conn.execute(f"""
        SELECT a.*, l.codigo_lote, p.nombre as producto_nombre
        FROM alertas_caducidad a
        JOIN lotes l ON a.lote_id = l.id
        JOIN productos p ON l.producto_id = p.id
        WHERE a.resuelto = 0 AND l.empresa_id = ?
          {lotes_centro_sql}
        ORDER BY a.fecha_alerta
    """, [empresa_id] + lotes_centro_params).fetchall()
    
    conn.close()
    
    return render_template('appcc/caducidades.html', 
                         proximos=proximos, 
                          caducados=caducados,
                          alertas=alertas)


@appcc_bp.route('/caducidades/generar-alertas', methods=['POST'])
@login_required
def generar_alertas_caducidad():
    conn = get_db()
    bloqueo = _requiere_gestion_appcc(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_appcc(conn)
    if bloqueo_centro:
        return bloqueo_centro
    empresa_id = session.get('user_empresa_id', 1)
    ensure_alertas_caducidad_schema(conn)
    lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, empresa_id, "l")

    proximos = conn.execute(f"""
        SELECT l.id
        FROM lotes l
        WHERE l.estado = 'activo'
          AND l.fecha_caducidad IS NOT NULL
          AND julianday(l.fecha_caducidad) - julianday('now') <= 7
          AND julianday(l.fecha_caducidad) - julianday('now') >= 0
          AND l.empresa_id = ?
          {lotes_centro_sql}
    """, [empresa_id] + lotes_centro_params).fetchall()

    caducados = conn.execute(f"""
        SELECT l.id
        FROM lotes l
        WHERE l.estado = 'activo'
          AND l.fecha_caducidad IS NOT NULL
          AND julianday(l.fecha_caducidad) - julianday('now') < 0
          AND l.empresa_id = ?
          {lotes_centro_sql}
    """, [empresa_id] + lotes_centro_params).fetchall()

    creadas = 0
    for lote in proximos:
        existe = conn.execute("""
            SELECT id FROM alertas_caducidad
            WHERE lote_id = ? AND tipo = 'proximo' AND resuelto = 0 AND empresa_id = ?
        """, (lote['id'], empresa_id)).fetchone()
        if not existe:
            conn.execute("""
                INSERT INTO alertas_caducidad (lote_id, fecha_alerta, tipo, empresa_id)
                VALUES (?, date('now'), 'proximo', ?)
            """, (lote['id'], empresa_id))
            creadas += 1

    for lote in caducados:
        existe = conn.execute("""
            SELECT id FROM alertas_caducidad
            WHERE lote_id = ? AND tipo = 'caducado' AND resuelto = 0 AND empresa_id = ?
        """, (lote['id'], empresa_id)).fetchone()
        if not existe:
            conn.execute("""
                INSERT INTO alertas_caducidad (lote_id, fecha_alerta, tipo, empresa_id)
                VALUES (?, date('now'), 'caducado', ?)
            """, (lote['id'], empresa_id))
            creadas += 1

    conn.commit()
    conn.close()
    flash(f'Alertas de caducidad actualizadas. Nuevas alertas: {creadas}.', 'success')
    return redirect(url_for('appcc.control_caducidades'))

@appcc_bp.route('/lotes/<int:lote_id>/marcar-consumido', methods=['POST'])
@login_required
def marcar_lote_consumido(lote_id):
    conn = get_db()
    bloqueo = _requiere_gestion_appcc(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_appcc(conn)
    if bloqueo_centro:
        return bloqueo_centro
    empresa_id = session.get('user_empresa_id', 1)
    ensure_alertas_caducidad_schema(conn)
    lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, empresa_id, "lotes")
    
    conn.execute(
        f"UPDATE lotes SET estado = 'consumido' WHERE id = ? AND empresa_id = ? {lotes_centro_sql}",
        [lote_id, empresa_id] + lotes_centro_params,
    )
    conn.execute(
        "UPDATE alertas_caducidad SET resuelto = 1 WHERE lote_id = ? AND empresa_id = ?",
        (lote_id, empresa_id),
    )
    conn.commit()
    conn.close()
    
    flash('✅ Lote marcado como consumido', 'success')
    return redirect(url_for('appcc.control_caducidades'))

# ============================================
# TRAZABILIDAD
# ============================================
@appcc_bp.route('/trazabilidad')
@login_required
def buscar_trazabilidad():
    return render_template('appcc/buscar_trazabilidad.html')


def _render_trazabilidad_lote(conn, lote_id, empresa_id):
    lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, empresa_id, "l")
    lote = conn.execute(f"""
        SELECT l.*, p.nombre AS producto_nombre, p.unidad,
               pr.nombre AS proveedor_nombre,
               a.nombre AS almacen_nombre
        FROM lotes l
        JOIN productos p ON p.id = l.producto_id
        LEFT JOIN proveedores pr ON pr.id = l.proveedor_id
        LEFT JOIN almacenes a ON a.id = l.almacen_id
        WHERE l.id = ? AND l.empresa_id = ?
          {lotes_centro_sql}
    """, [lote_id, empresa_id] + lotes_centro_params).fetchone()

    if not lote:
        flash('Lote no encontrado.', 'warning')
        return redirect(url_for('appcc.buscar_trazabilidad'))

    movimientos = conn.execute("""
        SELECT *
        FROM movimientos_lotes
        WHERE lote_id = ?
        ORDER BY datetime(fecha) DESC
    """, (lote_id,)).fetchall()

    usado_en = []
    if _table_exists(conn, "composicion_lotes"):
        usado_en = conn.execute("""
            SELECT cl.*, p.nombre AS producto_final, lp.id AS lote_producto,
                   lp.codigo_lote AS codigo_lote_producto
            FROM composicion_lotes cl
            JOIN lotes lp ON lp.id = cl.lote_producto_id
            JOIN productos p ON p.id = lp.producto_id
            WHERE cl.lote_ingrediente_id = ?
            ORDER BY cl.id DESC
        """, (lote_id,)).fetchall()

    return render_template(
        'appcc/trazabilidad_lote.html',
        lote=lote,
        movimientos=movimientos,
        usado_en=usado_en,
    )


@appcc_bp.route('/trazabilidad/lote')
@login_required
def trazabilidad_lote_buscar():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    codigo_lote = (request.args.get('codigo_lote') or '').strip()
    if not codigo_lote:
        conn.close()
        return redirect(url_for('appcc.buscar_trazabilidad'))

    lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, empresa_id, "l")
    lote = conn.execute(f"""
        SELECT id
        FROM lotes l
        WHERE empresa_id = ?
          AND (
                codigo_lote = ?
                OR lote_proveedor = ?
                OR documento_origen = ?
              )
          {lotes_centro_sql}
        ORDER BY id DESC
        LIMIT 1
    """, [empresa_id, codigo_lote, codigo_lote, codigo_lote] + lotes_centro_params).fetchone()
    if not lote:
        conn.close()
        flash('No se encontro ningun lote con ese codigo.', 'warning')
        return redirect(url_for('appcc.buscar_trazabilidad'))

    response = _render_trazabilidad_lote(conn, lote["id"], empresa_id)
    conn.close()
    return response


@appcc_bp.route('/trazabilidad/lote/<int:lote_id>')
@login_required
def trazabilidad_lote(lote_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    response = _render_trazabilidad_lote(conn, lote_id, empresa_id)
    conn.close()
    return response


# ============================================
# EXPORTAR INFORMES APPCC
# ============================================
@appcc_bp.route('/exportar/<string:tipo>')
@login_required
def exportar_informe_appcc(tipo):
    """Exporta informes APPCC a Excel"""
    from io import BytesIO
    import pandas as pd
    from flask import send_file
    
    empresa_id = session.get('user_empresa_id', 1)
    conn = get_db()
    
    fecha_desde = request.args.get('desde', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    fecha_hasta = request.args.get('hasta', datetime.now().strftime('%Y-%m-%d'))
    equipos_centro_sql, equipos_centro_params = _filtro_equipos_centro(conn, empresa_id, "e")
    limpieza_centro_sql, limpieza_centro_params = _filtro_plan_limpieza_centro(conn, empresa_id, "pl")
    lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, empresa_id, "l")
    appcc_centro_sql, appcc_centro_params = _filtro_appcc_centro(conn, empresa_id, "c")
    
    if tipo == 'temperaturas':
        df = pd.read_sql_query(f"""
            SELECT date(rt.fecha) as Fecha, time(rt.fecha) as Hora,
                e.nombre as Equipo, e.tipo as Tipo,
                rt.temperatura as Temperatura,
                e.rango_min as Min, e.rango_max as Max,
                CASE WHEN rt.fuera_rango THEN 'FUERA' ELSE 'OK' END as Estado,
                rt.usuario as Usuario, rt.observaciones as Observaciones
            FROM registros_temperatura rt
            JOIN equipos e ON rt.equipo_id = e.id
            WHERE date(rt.fecha) BETWEEN ? AND ? 
            AND e.empresa_id = ?
            {equipos_centro_sql}
            ORDER BY rt.fecha DESC
        """, conn, params=[fecha_desde, fecha_hasta, empresa_id] + equipos_centro_params)
        filename = f'temperaturas_{fecha_desde}_a_{fecha_hasta}.xlsx'
    
    elif tipo == 'limpieza':
        df = pd.read_sql_query(f"""
            SELECT date(rl.fecha) as Fecha, time(rl.fecha) as Hora,
                pl.area as Area, pl.tarea as Tarea,
                pl.frecuencia as Frecuencia,
                rl.realizada_por as Realizado_por,
                rl.observaciones as Observaciones
            FROM registros_limpieza rl
            JOIN plan_limpieza pl ON rl.plan_id = pl.id
            WHERE date(rl.fecha) BETWEEN ? AND ? 
            AND pl.empresa_id = ?
            {limpieza_centro_sql}
            ORDER BY rl.fecha DESC
        """, conn, params=[fecha_desde, fecha_hasta, empresa_id] + limpieza_centro_params)
        filename = f'limpieza_{fecha_desde}_a_{fecha_hasta}.xlsx'
    
    elif tipo == 'caducidades':
        df = pd.read_sql_query(f"""
            SELECT l.codigo_lote as Lote,
                p.nombre as Producto,
                l.fecha_caducidad as Caducidad,
                l.cantidad_actual as Stock,
                p.unidad as Unidad,
                julianday('now') - julianday(l.fecha_caducidad) as Dias_caducado,
                CASE 
                    WHEN julianday(l.fecha_caducidad) - julianday('now') < 0 THEN 'CADUCADO'
                    WHEN julianday(l.fecha_caducidad) - julianday('now') <= 7 THEN 'PRÓXIMO'
                    ELSE 'OK'
                END as Estado
            FROM lotes l
            JOIN productos p ON l.producto_id = p.id
            WHERE l.estado = 'activo' AND l.empresa_id = ?
              {lotes_centro_sql}
            ORDER BY l.fecha_caducidad
        """, conn, params=[empresa_id] + lotes_centro_params)
        filename = f'caducidades_{datetime.now().strftime("%Y%m%d")}.xlsx'

    elif tipo == 'plan':
        ensure_appcc_plan_schema(conn)
        df = pd.read_sql_query(f"""
            SELECT date(r.fecha) as Fecha,
                time(r.fecha) as Hora,
                c.codigo as Codigo,
                c.area as Area,
                c.etapa as Etapa,
                c.tipo_control as Tipo_control,
                c.limite_critico as Limite_critico,
                r.valor as Valor,
                r.resultado as Resultado,
                r.realizado_por as Realizado_por,
                r.observaciones as Observaciones,
                r.accion_correctiva as Accion_correctiva,
                r.verificado_por as Verificado_por,
                r.fecha_verificacion as Fecha_verificacion,
                r.origen_tabla as Origen_tabla,
                r.origen_id as Origen_id,
                r.codigo_operacion as Codigo_operacion,
                r.incidencia_id as Incidencia
            FROM appcc_registros_plan r
            JOIN appcc_controles_plan c ON c.id = r.control_id
            WHERE date(r.fecha) BETWEEN ? AND ?
              AND r.empresa_id = ?
              {appcc_centro_sql}
            ORDER BY r.fecha DESC
        """, conn, params=[fecha_desde, fecha_hasta, empresa_id] + appcc_centro_params)
        filename = f'plan_appcc_{fecha_desde}_a_{fecha_hasta}.xlsx'

    elif tipo == 'operativa':
        ensure_appcc_plan_schema(conn)
        filtros = ["date(r.fecha) BETWEEN ? AND ?", "r.empresa_id = ?"]
        params = [fecha_desde, fecha_hasta, empresa_id]
        if appcc_centro_sql:
            filtros.append(appcc_centro_sql.replace(" AND ", "", 1))
            params.extend(appcc_centro_params)
        origen = (request.args.get('origen') or '').strip()
        resultado = (request.args.get('resultado') or '').strip()
        codigo = (request.args.get('codigo') or '').strip()
        q = (request.args.get('q') or '').strip()
        if origen:
            if origen == "manual":
                filtros.append("(r.origen_tabla IS NULL OR r.origen_tabla = '')")
            else:
                filtros.append("r.origen_tabla = ?")
                params.append(origen)
        if resultado:
            filtros.append("COALESCE(r.resultado, 'ok') = ?")
            params.append(resultado)
        if codigo:
            filtros.append("c.codigo = ?")
            params.append(codigo)
        if q:
            like = f"%{q}%"
            filtros.append("""
                (
                    c.codigo LIKE ?
                    OR c.area LIKE ?
                    OR c.etapa LIKE ?
                    OR r.valor LIKE ?
                    OR r.observaciones LIKE ?
                    OR r.accion_correctiva LIKE ?
                    OR i.descripcion LIKE ?
                )
            """)
            params.extend([like, like, like, like, like, like, like])
        where_sql = " AND ".join(filtros)
        df = pd.read_sql_query(f"""
            SELECT date(r.fecha) AS Fecha,
                time(r.fecha) AS Hora,
                COALESCE(NULLIF(r.origen_tabla, ''), 'manual') AS Operativa,
                r.origen_id AS Origen_id,
                c.codigo AS Control,
                c.area AS Area,
                c.etapa AS Etapa,
                c.tipo_control AS Tipo_control,
                r.resultado AS Resultado,
                r.valor AS Valor,
                r.realizado_por AS Realizado_por,
                r.observaciones AS Observaciones,
                r.accion_correctiva AS Accion_correctiva,
                r.verificado_por AS Verificado_por,
                r.incidencia_id AS Incidencia,
                i.estado AS Estado_incidencia,
                i.gravedad AS Gravedad
            FROM appcc_registros_plan r
            JOIN appcc_controles_plan c ON c.id = r.control_id
            LEFT JOIN incidencias i ON i.id = r.incidencia_id
            WHERE {where_sql}
            ORDER BY r.fecha DESC
        """, conn, params=params)
        filename = f'operativa_appcc_{fecha_desde}_a_{fecha_hasta}.xlsx'
    
    else:
        conn.close()
        flash('Tipo de informe no válido', 'danger')
        return redirect(request.referrer or '/')
    
    conn.close()
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name=tipo.capitalize(), index=False)
    
    output.seek(0)
    
    return send_file(
        output,
        download_name=filename,
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

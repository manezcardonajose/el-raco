# blueprints/produccion/routes.py
from flask import render_template, request, redirect, url_for, session, flash, abort
from database import get_db
from datetime import datetime, timedelta
import math
import unicodedata
from . import produccion_bp
from blueprints.auth.routes import login_required, normalizar_rol, usuario_es_global_operacion, usuario_tiene_permiso
from helpers.appcc_plan_helper import registrar_appcc_control
from helpers.etiquetas_helper import build_label_css, get_current_print_device, get_label_config, get_label_configs
from helpers.label_raw_helper import zpl_lote_elaborado
from helpers.centros_helper import centro_activo_id, ids_departamentos_centro
from helpers.receta_traducciones import traducir_nombre_receta
from helpers.ambito_helper import filtro_ambito_operativo, ids_puntos_venta_ambito
from ia.alergenos import ALERGENOS_UE, detectar_alergenos


ROLES_PRODUCCION_COCINA = {
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


@produccion_bp.before_request
def _bloquear_produccion_cocina_sin_rol():
    if "user_id" not in session:
        return None
    if _rol_actual_key() in ROLES_PRODUCCION_COCINA:
        return None
    flash("No tienes permisos para gestionar partes de produccion de cocina.", "warning")
    return redirect(url_for("home"))


def _puede_gestionar_produccion():
    return (
        _rol_actual_key() in ROLES_PRODUCCION_COCINA
        or usuario_tiene_permiso("produccion_editar")
        or usuario_tiene_permiso("acceso_global")
        or usuario_es_global_operacion()
    )


def _requiere_gestion_produccion(conn=None, destino="produccion.produccion_index"):
    if _puede_gestionar_produccion():
        return None
    if conn:
        conn.close()
    flash("No tienes permisos para modificar produccion.", "warning")
    return redirect(url_for(destino))


def _requiere_centro_produccion(conn=None, destino="produccion.produccion_index"):
    if centro_activo_id():
        return None
    if conn:
        conn.close()
    flash("Selecciona un centro antes de modificar produccion.", "warning")
    return redirect(url_for("rrhh.centro_empresa_general"))


def _columnas_tabla(cursor, tabla):
    try:
        return [row[1] for row in cursor.execute(f"PRAGMA table_info({tabla})").fetchall()]
    except Exception:
        return []


def _safe_add_column(cursor, tabla, definicion):
    nombre = definicion.strip().split()[0]
    if nombre not in _columnas_tabla(cursor, tabla):
        cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN {definicion}")


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _scope_partes_produccion(conn, alias, empresa_id):
    return filtro_ambito_operativo(
        conn,
        alias,
        empresa_id,
        "partes_produccion",
        departamento_col=None,
        subdepartamento_col="subdepartamento_id",
        punto_venta_col="punto_venta_id",
    )


def _filtro_centro_tabla(conn, tabla, alias=""):
    centro_id = centro_activo_id()
    if not centro_id:
        return "", []
    if "centro_id" not in _columnas_tabla(conn.cursor(), tabla):
        return ' AND 1 = 0', []
    prefijo = f"{alias}." if alias else ""
    return f" AND {prefijo}centro_id = ?", [centro_id]


def _filtro_producto_visible_centro(conn, alias="p", centro_id=None):
    centro_id = centro_id or centro_activo_id()
    if not centro_id or "centro_id" not in _columnas_tabla(conn.cursor(), "productos"):
        return " AND 1 = 0", []
    prefijo = f"{alias}." if alias else ""
    return f" AND COALESCE({prefijo}centro_id, 0) IN (0, ?)", [centro_id]


def _filtro_lotes_centro(conn, alias="l", centro_id=None):
    centro_id = centro_id or centro_activo_id()
    if not centro_id:
        return "", "", []
    if "centro_id" not in _columnas_tabla(conn.cursor(), "almacenes"):
        return '', ' AND 1 = 0', []
    prefijo = f"{alias}." if alias else ""
    return (
        "LEFT JOIN almacenes al_lote_scope ON al_lote_scope.id = {prefijo}almacen_id".format(prefijo=prefijo),
        " AND al_lote_scope.centro_id = ? AND al_lote_scope.empresa_id = {prefijo}empresa_id".format(prefijo=prefijo),
        [centro_id],
    )


def _usuario_puede_terminal_subdepartamento(subdepartamento):
    if not subdepartamento:
        return False
    if usuario_es_global_operacion() or usuario_tiene_permiso("acceso_global"):
        return True

    rol = normalizar_rol(session.get("user_rol"))
    departamento_usuario = session.get("user_departamento_id")
    subdepartamento_usuario = session.get("user_subdepartamento_id")
    departamento_sub = subdepartamento["departamento_id"] if "departamento_id" in subdepartamento.keys() else None
    subdepartamento_sub = subdepartamento["id"] if "id" in subdepartamento.keys() else None

    roles_jefatura_departamento = {"jefe_cocina", "chef", "jefe_departamento", "segundo_jefe"}
    if rol in roles_jefatura_departamento and departamento_usuario and departamento_sub and str(departamento_usuario) == str(departamento_sub):
        return True
    if subdepartamento_usuario and subdepartamento_sub and str(subdepartamento_usuario) == str(subdepartamento_sub):
        return True

    # Usuarios antiguos sin empleado vinculado: conserva acceso si tienen permiso de produccion.
    if not departamento_usuario and not subdepartamento_usuario:
        return usuario_tiene_permiso("produccion_ver") or usuario_tiene_permiso("produccion_editar")
    return False


def _asegurar_planificacion_schema(conn):
    cursor = conn.cursor()
    _safe_add_column(cursor, "recetas", "raciones_base REAL DEFAULT 4")
    _safe_add_column(cursor, "recetas", "escandallo_fiable INTEGER DEFAULT 1")
    _safe_add_column(cursor, "recetas", "origen_escandallo TEXT")
    _safe_add_column(cursor, "recetas", "coste_validado INTEGER DEFAULT 0")
    _safe_add_column(cursor, "recetas", "centro_id INTEGER")
    _safe_add_column(cursor, "recetas", "nombre_en TEXT")
    _safe_add_column(cursor, "recetas", "nombre_fr TEXT")
    _safe_add_column(cursor, "recetas", "nombre_de TEXT")
    _safe_add_column(cursor, "recetas", "nombre_it TEXT")
    _safe_add_column(cursor, "productos", "centro_id INTEGER")
    _safe_add_column(cursor, "partes_produccion", "centro_id INTEGER")
    _safe_add_column(cursor, "planificacion_servicios", "centro_id INTEGER")
    _safe_add_column(cursor, "partes_produccion", "subdepartamento_id INTEGER")
    _safe_add_column(cursor, "partes_produccion", "pax_previstos REAL")
    _safe_add_column(cursor, "partes_produccion", "regla_calculo TEXT")
    _safe_add_column(cursor, "lineas_produccion", "estado TEXT DEFAULT 'pendiente'")
    _safe_add_column(cursor, "lineas_produccion", "raciones_planificadas REAL")
    _safe_add_column(cursor, "lineas_produccion", "pax_previstos REAL")
    _safe_add_column(cursor, "lineas_produccion", "estacion_buffet TEXT")
    _safe_add_column(cursor, "lineas_produccion", "porcentaje_prevision REAL")
    _safe_add_column(cursor, "lineas_produccion", "regla_calculo TEXT")
    _safe_add_column(cursor, "lineas_produccion", "hora_inicio TEXT")
    _safe_add_column(cursor, "lineas_produccion", "hora_fin TEXT")
    _safe_add_column(cursor, "lineas_produccion", "operario TEXT")
    _safe_add_column(cursor, "productos", "tiempo_descongelado_horas INTEGER DEFAULT 0")
    _safe_add_column(cursor, "productos", "requiere_desinfeccion INTEGER DEFAULT 0")
    _safe_add_column(cursor, "productos", "control_lote INTEGER DEFAULT 1")
    _safe_add_column(cursor, "productos", "control_caducidad INTEGER DEFAULT 0")
    _safe_add_column(cursor, "productos", "alergenos TEXT")
    _safe_add_column(cursor, "productos", "alergenos_validado INTEGER DEFAULT 0")
    _safe_add_column(cursor, "lotes", "fecha_descongelado DATETIME")
    _safe_add_column(cursor, "lotes", "estado_descongelado TEXT DEFAULT 'pendiente'")
    _safe_add_column(cursor, "lotes", "fecha_ultimo_control DATETIME")
    _safe_add_column(cursor, "lotes", "temperatura_descongelado REAL")
    _safe_add_column(cursor, "lotes", "responsable_recepcion TEXT")
    _safe_add_column(cursor, "lotes", "ubicacion_actual TEXT")
    _safe_add_column(cursor, "lotes", "incidencia_id INTEGER")
    _safe_add_column(cursor, "lotes", "documento_origen TEXT")
    _safe_add_column(cursor, "lotes", "notas_trazabilidad TEXT")
    _safe_add_column(cursor, "lotes", "coste_unitario REAL DEFAULT 0")
    _safe_add_column(cursor, "lotes", "coste_total REAL DEFAULT 0")
    _safe_add_column(cursor, "lotes", "moneda TEXT DEFAULT 'EUR'")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alertas_programadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            lote_id INTEGER NOT NULL,
            parte_id INTEGER,
            fecha_alerta DATETIME NOT NULL,
            fecha_produccion DATE,
            mensaje TEXT,
            enviada INTEGER DEFAULT 0,
            resuelta INTEGER DEFAULT 0,
            empresa_id INTEGER DEFAULT 1,
            creada_en DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lote_id) REFERENCES lotes(id)
        )
    """)
    _safe_add_column(cursor, "alertas_programadas", "parte_id INTEGER")
    _safe_add_column(cursor, "alertas_programadas", "fecha_produccion DATE")
    _safe_add_column(cursor, "alertas_programadas", "mensaje TEXT")
    _safe_add_column(cursor, "alertas_programadas", "enviada INTEGER DEFAULT 0")
    _safe_add_column(cursor, "alertas_programadas", "resuelta INTEGER DEFAULT 0")
    _safe_add_column(cursor, "alertas_programadas", "empresa_id INTEGER DEFAULT 1")
    _safe_add_column(cursor, "alertas_programadas", "creada_en DATETIME")
    _safe_add_column(cursor, "alertas_programadas", "cantidad_necesaria REAL DEFAULT 0")
    _safe_add_column(cursor, "alertas_programadas", "unidad TEXT")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movimientos_lotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            tipo TEXT NOT NULL,
            cantidad REAL NOT NULL,
            destino_tipo TEXT,
            destino_id INTEGER,
            usuario TEXT,
            observaciones TEXT,
            documento TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (lote_id) REFERENCES lotes(id)
        )
    """)
    _safe_add_column(cursor, "movimientos_lotes", "documento TEXT")
    _safe_add_column(cursor, "movimientos_lotes", "empresa_id INTEGER DEFAULT 1")
    _safe_add_column(cursor, "consumo_produccion", "producto_id INTEGER")
    _safe_add_column(cursor, "consumo_produccion", "coste_unitario REAL DEFAULT 0")
    _safe_add_column(cursor, "consumo_produccion", "coste_total REAL DEFAULT 0")
    _safe_add_column(cursor, "consumo_produccion", "empresa_id INTEGER DEFAULT 1")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS produccion_ingredientes_reales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            linea_produccion_id INTEGER NOT NULL,
            receta_ingrediente_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad_planificada REAL DEFAULT 0,
            cantidad_real REAL DEFAULT 0,
            observaciones TEXT,
            usuario TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(linea_produccion_id, receta_ingrediente_id),
            FOREIGN KEY (linea_produccion_id) REFERENCES lineas_produccion(id) ON DELETE CASCADE,
            FOREIGN KEY (receta_ingrediente_id) REFERENCES recetas_ingredientes(id)
        )
    """)
    _safe_add_column(cursor, "produccion_ingredientes_reales", "producto_id INTEGER")
    _safe_add_column(cursor, "produccion_ingredientes_reales", "cantidad_planificada REAL DEFAULT 0")
    _safe_add_column(cursor, "produccion_ingredientes_reales", "cantidad_real REAL DEFAULT 0")
    _safe_add_column(cursor, "produccion_ingredientes_reales", "observaciones TEXT")
    _safe_add_column(cursor, "produccion_ingredientes_reales", "usuario TEXT")
    _safe_add_column(cursor, "produccion_ingredientes_reales", "empresa_id INTEGER DEFAULT 1")
    _safe_add_column(cursor, "produccion_ingredientes_reales", "fecha DATETIME DEFAULT CURRENT_TIMESTAMP")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registros_descongelado (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha_control DATETIME DEFAULT CURRENT_TIMESTAMP,
            temperatura REAL,
            aspecto TEXT,
            olfativo TEXT,
            decision TEXT,
            responsable TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (lote_id) REFERENCES lotes(id)
        )
    """)
    _safe_add_column(cursor, "registros_descongelado", "empresa_id INTEGER DEFAULT 1")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registros_desinfeccion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            producto_usado TEXT,
            dosis TEXT,
            tiempo_contacto TEXT,
            aclarado INTEGER DEFAULT 1,
            resultado TEXT,
            responsable TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (lote_id) REFERENCES lotes(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS produccion_controles_appcc (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parte_id INTEGER NOT NULL,
            linea_produccion_id INTEGER NOT NULL UNIQUE,
            receta_id INTEGER,
            responsable TEXT,
            hora_inicio_elaboracion TEXT,
            hora_fin_elaboracion TEXT,
            temperatura_fin_elaboracion REAL,
            destino_sobrante TEXT DEFAULT 'sin_sobrante',
            cantidad_sobrante REAL DEFAULT 0,
            unidad_sobrante TEXT,
            motivo_merma TEXT,
            metodo_conservacion TEXT DEFAULT 'servicio_directo',
            temperatura_inicio_conservacion REAL,
            temperatura_fin_conservacion REAL,
            ubicacion_conservacion TEXT,
            tipo_envase TEXT DEFAULT 'recipiente_tapado',
            dias_vida_util REAL,
            fecha_envasado TEXT,
            fecha_limite_uso TEXT,
            requiere_regeneracion INTEGER DEFAULT 0,
            temperatura_regeneracion REAL,
            tiempo_regeneracion_min REAL,
            temperatura_servicio REAL,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            actualizado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parte_id) REFERENCES partes_produccion(id) ON DELETE CASCADE,
            FOREIGN KEY (linea_produccion_id) REFERENCES lineas_produccion(id) ON DELETE CASCADE,
            FOREIGN KEY (receta_id) REFERENCES recetas(id)
        )
    """)
    for column_sql in (
        "responsable TEXT",
        "hora_inicio_elaboracion TEXT",
        "hora_fin_elaboracion TEXT",
        "temperatura_fin_elaboracion REAL",
        "destino_sobrante TEXT DEFAULT 'sin_sobrante'",
        "cantidad_sobrante REAL DEFAULT 0",
        "unidad_sobrante TEXT",
        "motivo_merma TEXT",
        "metodo_conservacion TEXT DEFAULT 'servicio_directo'",
        "temperatura_inicio_conservacion REAL",
        "temperatura_fin_conservacion REAL",
        "ubicacion_conservacion TEXT",
        "tipo_envase TEXT DEFAULT 'recipiente_tapado'",
        "dias_vida_util REAL",
        "fecha_envasado TEXT",
        "fecha_limite_uso TEXT",
        "requiere_regeneracion INTEGER DEFAULT 0",
        "temperatura_regeneracion REAL",
        "tiempo_regeneracion_min REAL",
        "temperatura_servicio REAL",
        "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "actualizado_en DATETIME DEFAULT CURRENT_TIMESTAMP",
    ):
        _safe_add_column(cursor, "produccion_controles_appcc", column_sql)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS produccion_lotes_elaborados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo_lote TEXT UNIQUE,
            parte_id INTEGER NOT NULL,
            linea_produccion_id INTEGER NOT NULL UNIQUE,
            receta_id INTEGER,
            cantidad REAL DEFAULT 0,
            unidad TEXT,
            metodo_conservacion TEXT,
            ubicacion TEXT,
            temperatura_inicio REAL,
            temperatura_fin REAL,
            tipo_envase TEXT DEFAULT 'recipiente_tapado',
            dias_vida_util REAL,
            fecha_elaboracion TEXT,
            fecha_limite_uso TEXT,
            estado TEXT DEFAULT 'disponible',
            responsable TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parte_id) REFERENCES partes_produccion(id) ON DELETE CASCADE,
            FOREIGN KEY (linea_produccion_id) REFERENCES lineas_produccion(id) ON DELETE CASCADE,
            FOREIGN KEY (receta_id) REFERENCES recetas(id)
        )
    """)
    for column_sql in (
        "codigo_lote TEXT",
        "cantidad REAL DEFAULT 0",
        "unidad TEXT",
        "metodo_conservacion TEXT",
        "ubicacion TEXT",
        "temperatura_inicio REAL",
        "temperatura_fin REAL",
        "tipo_envase TEXT DEFAULT 'recipiente_tapado'",
        "dias_vida_util REAL",
        "fecha_elaboracion TEXT",
        "fecha_limite_uso TEXT",
        "estado TEXT DEFAULT 'disponible'",
        "responsable TEXT",
        "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha DATETIME DEFAULT CURRENT_TIMESTAMP",
    ):
        _safe_add_column(cursor, "produccion_lotes_elaborados", column_sql)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS produccion_regeneraciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_elaborado_id INTEGER,
            linea_produccion_id INTEGER,
            fecha_inicio TEXT,
            fecha_fin TEXT,
            temperatura_inicio REAL,
            temperatura_fin REAL,
            tiempo_min REAL,
            responsable TEXT,
            resultado TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lote_elaborado_id) REFERENCES produccion_lotes_elaborados(id),
            FOREIGN KEY (linea_produccion_id) REFERENCES lineas_produccion(id)
        )
    """)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS produccion_salidas_sobrantes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL, empresa_id INTEGER NOT NULL,
            destino TEXT NOT NULL, referencia TEXT NOT NULL,
            cantidad REAL NOT NULL, unidad TEXT NOT NULL,
            responsable TEXT NOT NULL, coste_transferido REAL,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')


def _destinar_sobrante(conn, lote, cantidad, destino, referencia, responsable):
    if destino not in {'comedor_personal', 'buffet', 'otra_elaboracion'}:
        raise ValueError('Destino de sobrante no valido')
    cantidad = _to_float(cantidad, -1)
    if not math.isfinite(cantidad) or cantidad <= 0 or not referencia.strip() or not responsable.strip():
        raise ValueError('Indica cantidad positiva, referencia del destino y responsable')
    limite = _parse_datetime_local(lote['fecha_limite_uso'])
    if not limite or limite < datetime.now() or not lote['ubicacion']:
        raise ValueError('El lote requiere ubicacion y un limite de uso vigente')
    coste = None
    if lote['unidad'] == 'raciones':
        origen = conn.execute('''
            SELECT coste_real, cantidad_real FROM lineas_produccion
            WHERE id=? AND empresa_id=?
        ''', (lote['linea_produccion_id'], lote['empresa_id'])).fetchone()
        if origen and origen['coste_real'] is not None and _to_float(origen['cantidad_real']) > 0:
            coste = round(cantidad * origen['coste_real'] / origen['cantidad_real'], 6)
    actualizado = conn.execute('''
        UPDATE produccion_lotes_elaborados
        SET cantidad=cantidad-?, estado=CASE WHEN cantidad-? <= 0 THEN 'consumido' ELSE estado END
        WHERE id=? AND empresa_id=? AND estado='disponible' AND cantidad>=?
    ''', (cantidad, cantidad, lote['id'], lote['empresa_id'], cantidad))
    if actualizado.rowcount != 1:
        raise ValueError('Saldo de sobrante insuficiente o lote no disponible')
    conn.execute('''
        INSERT INTO produccion_salidas_sobrantes
        (lote_id,empresa_id,destino,referencia,cantidad,unidad,responsable,coste_transferido)
        VALUES(?,?,?,?,?,?,?,?)
    ''', (lote['id'],lote['empresa_id'],destino,referencia.strip(),cantidad,lote['unidad'],responsable.strip(),coste))


def _to_datetime_local(valor):
    texto = str(valor or "").strip()
    if not texto:
        return ""
    texto = texto.replace(" ", "T")
    return texto[:16]


def _parse_datetime_local(valor):
    texto = str(valor or "").strip()
    if not texto:
        return None
    texto = texto.replace("T", " ")
    for candidato, fmt in (
        (texto[:19], "%Y-%m-%d %H:%M:%S"),
        (texto[:16], "%Y-%m-%d %H:%M"),
        (texto[:10], "%Y-%m-%d"),
    ):
        try:
            return datetime.strptime(candidato, fmt)
        except Exception:
            continue
    return None


def _dias_vida_util_defecto(metodo_conservacion, tipo_envase):
    metodo = _normalizar_texto(metodo_conservacion)
    envase = _normalizar_texto(tipo_envase)
    if "vacio" in envase:
        return 18
    if metodo == "congelado" or "congel" in envase:
        return 90
    if metodo in {"abatido", "frio"}:
        return 3
    if metodo == "caliente":
        return 0
    return 1


def _calcular_fecha_limite(fecha_base, dias_vida_util):
    base = _parse_datetime_local(fecha_base) or datetime.now()
    dias = _to_float(dias_vida_util, 0.0)
    return (base + timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M")


def _validar_sobrante(conn, linea, destino, cantidad, unidad, ubicacion='', metodo='', envase='', fecha='', limite=''):
    error = None
    producido = linea['cantidad_real'] if linea['cantidad_real'] is not None else linea['cantidad_planificada']
    if 'raciones_planificadas' in linea.keys() and linea['raciones_planificadas'] is not None:
        producido = _to_float(linea['raciones_planificadas'], 0)
        if linea['cantidad_real'] is not None and _to_float(linea['cantidad_planificada'], 0) > 0:
            producido *= _to_float(linea['cantidad_real'], 0) / _to_float(linea['cantidad_planificada'], 1)
    if not math.isfinite(cantidad) or cantidad < 0:
        error = 'La cantidad sobrante debe ser un numero positivo o cero.'
    elif unidad == 'raciones' and producido is not None and cantidad > producido:
        error = 'Las raciones sobrantes no pueden superar las producidas.'
    elif destino == 'reaprovechamiento' and cantidad > 0:
        inicio = _parse_datetime_local(fecha)
        fin = _parse_datetime_local(limite)
        if not ubicacion or not metodo or metodo == 'servicio_directo' or not envase or not inicio or not fin or fin <= inicio:
            error = 'Para guardar sobrantes indica ubicacion, conservacion, envase y fechas validas en el control APPCC.'
    if error:
        conn.rollback()
        abort(400, description=error)


def _guardar_control_appcc_linea(conn, parte, linea, empresa_id):
    if conn.execute('SELECT 1 FROM produccion_salidas_sobrantes s JOIN produccion_lotes_elaborados l ON l.id=s.lote_id WHERE l.linea_produccion_id=? AND s.empresa_id=? LIMIT 1', (linea['id'], empresa_id)).fetchone():
        return
    linea_id = linea["id"]
    responsable = session.get("user_nombre") or parte["responsable"] or "admin"
    hora_inicio = (request.form.get(f"hora_inicio_elaboracion_{linea_id}") or "").strip()
    hora_fin = (request.form.get(f"hora_fin_elaboracion_{linea_id}") or "").strip()
    temperatura_elaboracion = _to_float(
        request.form.get(f"temperatura_fin_elaboracion_{linea_id}"),
        None,
    )
    control_actual = conn.execute(
        "SELECT * FROM produccion_controles_appcc WHERE linea_produccion_id = ?",
        (linea_id,),
    ).fetchone()
    if f"destino_sobrante_{linea_id}" in request.form:
        destino_sobrante = request.form.get(f"destino_sobrante_{linea_id}") or "sin_sobrante"
        cantidad_sobrante = _to_float(request.form.get(f"cantidad_sobrante_{linea_id}"), 0.0)
        unidad_sobrante = (request.form.get(f"unidad_sobrante_{linea_id}") or "raciones").strip()
        motivo_merma = (request.form.get(f"motivo_merma_{linea_id}") or "").strip()
    else:
        destino_sobrante = control_actual["destino_sobrante"] if control_actual else "sin_sobrante"
        cantidad_sobrante = _to_float(control_actual["cantidad_sobrante"] if control_actual else None, 0.0)
        unidad_sobrante = (control_actual["unidad_sobrante"] if control_actual else None) or "raciones"
        motivo_merma = (control_actual["motivo_merma"] if control_actual else "") or ""
    metodo_conservacion = request.form.get(f"metodo_conservacion_{linea_id}") or "servicio_directo"
    temp_inicio = _to_float(request.form.get(f"temperatura_inicio_conservacion_{linea_id}"), None)
    temp_fin = _to_float(request.form.get(f"temperatura_fin_conservacion_{linea_id}"), None)
    ubicacion = (request.form.get(f"ubicacion_conservacion_{linea_id}") or "").strip()
    tipo_envase = request.form.get(f"tipo_envase_{linea_id}") or "recipiente_tapado"
    dias_form = (request.form.get(f"dias_vida_util_{linea_id}") or "").strip()
    dias_vida_util = _to_float(dias_form, _dias_vida_util_defecto(metodo_conservacion, tipo_envase))
    fecha_envasado = (request.form.get(f"fecha_envasado_{linea_id}") or hora_fin or hora_inicio or str(parte["fecha"] or "")).strip()
    fecha_limite = (request.form.get(f"fecha_limite_uso_{linea_id}") or "").strip()
    if not fecha_limite:
        fecha_limite = _calcular_fecha_limite(fecha_envasado, dias_vida_util)
    requiere_regeneracion = 1 if request.form.get(f"requiere_regeneracion_{linea_id}") else 0
    temp_regeneracion = _to_float(request.form.get(f"temperatura_regeneracion_{linea_id}"), None)
    tiempo_regeneracion = _to_float(request.form.get(f"tiempo_regeneracion_min_{linea_id}"), None)
    temp_servicio = _to_float(request.form.get(f"temperatura_servicio_{linea_id}"), None)
    observaciones = (request.form.get(f"control_observaciones_{linea_id}") or "").strip()

    _validar_sobrante(conn, linea, destino_sobrante, cantidad_sobrante, unidad_sobrante,
                     ubicacion, metodo_conservacion, tipo_envase, fecha_envasado, fecha_limite)

    conn.execute("""
        INSERT INTO produccion_controles_appcc (
            parte_id, linea_produccion_id, receta_id, responsable,
            hora_inicio_elaboracion, hora_fin_elaboracion, temperatura_fin_elaboracion,
            destino_sobrante, cantidad_sobrante, unidad_sobrante, motivo_merma,
            metodo_conservacion, temperatura_inicio_conservacion, temperatura_fin_conservacion,
            ubicacion_conservacion, tipo_envase, dias_vida_util, fecha_envasado,
            fecha_limite_uso, requiere_regeneracion,
            temperatura_regeneracion, tiempo_regeneracion_min, temperatura_servicio,
            observaciones, empresa_id, actualizado_en
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(linea_produccion_id)
        DO UPDATE SET
            receta_id = excluded.receta_id,
            responsable = excluded.responsable,
            hora_inicio_elaboracion = excluded.hora_inicio_elaboracion,
            hora_fin_elaboracion = excluded.hora_fin_elaboracion,
            temperatura_fin_elaboracion = excluded.temperatura_fin_elaboracion,
            destino_sobrante = excluded.destino_sobrante,
            cantidad_sobrante = excluded.cantidad_sobrante,
            unidad_sobrante = excluded.unidad_sobrante,
            motivo_merma = excluded.motivo_merma,
            metodo_conservacion = excluded.metodo_conservacion,
            temperatura_inicio_conservacion = excluded.temperatura_inicio_conservacion,
            temperatura_fin_conservacion = excluded.temperatura_fin_conservacion,
            ubicacion_conservacion = excluded.ubicacion_conservacion,
            tipo_envase = excluded.tipo_envase,
            dias_vida_util = excluded.dias_vida_util,
            fecha_envasado = excluded.fecha_envasado,
            fecha_limite_uso = excluded.fecha_limite_uso,
            requiere_regeneracion = excluded.requiere_regeneracion,
            temperatura_regeneracion = excluded.temperatura_regeneracion,
            tiempo_regeneracion_min = excluded.tiempo_regeneracion_min,
            temperatura_servicio = excluded.temperatura_servicio,
            observaciones = excluded.observaciones,
            empresa_id = excluded.empresa_id,
            actualizado_en = datetime('now')
    """, (
        parte["id"],
        linea_id,
        linea["receta_id"],
        responsable,
        hora_inicio,
        hora_fin,
        temperatura_elaboracion,
        destino_sobrante,
        cantidad_sobrante,
        unidad_sobrante,
        motivo_merma,
        metodo_conservacion,
        temp_inicio,
        temp_fin,
        ubicacion,
        tipo_envase,
        dias_vida_util,
        fecha_envasado,
        fecha_limite,
        requiere_regeneracion,
        temp_regeneracion,
        tiempo_regeneracion,
        temp_servicio,
        observaciones,
        empresa_id,
    ))

    if destino_sobrante == "reaprovechamiento" and cantidad_sobrante > 0:
        fecha_txt = str(parte["fecha"] or datetime.now().strftime("%Y-%m-%d"))[:10].replace("-", "")
        codigo_lote = f"ELAB-{fecha_txt}-{linea_id}"
        conn.execute("""
            INSERT INTO produccion_lotes_elaborados (
                codigo_lote, parte_id, linea_produccion_id, receta_id,
                cantidad, unidad, metodo_conservacion, ubicacion,
                temperatura_inicio, temperatura_fin, tipo_envase, dias_vida_util,
                fecha_elaboracion,
                fecha_limite_uso, estado, responsable, observaciones,
                empresa_id, fecha
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'disponible', ?, ?, ?, datetime('now'))
            ON CONFLICT(linea_produccion_id)
            DO UPDATE SET
                codigo_lote = excluded.codigo_lote,
                cantidad = excluded.cantidad,
                unidad = excluded.unidad,
                metodo_conservacion = excluded.metodo_conservacion,
                ubicacion = excluded.ubicacion,
                temperatura_inicio = excluded.temperatura_inicio,
                temperatura_fin = excluded.temperatura_fin,
                tipo_envase = excluded.tipo_envase,
                dias_vida_util = excluded.dias_vida_util,
                fecha_elaboracion = excluded.fecha_elaboracion,
                fecha_limite_uso = excluded.fecha_limite_uso,
                estado = 'disponible',
                responsable = excluded.responsable,
                observaciones = excluded.observaciones,
                empresa_id = excluded.empresa_id,
                fecha = datetime('now')
        """, (
            codigo_lote,
            parte["id"],
            linea_id,
            linea["receta_id"],
            cantidad_sobrante,
            unidad_sobrante,
            metodo_conservacion,
            ubicacion,
            temp_inicio,
            temp_fin,
            tipo_envase,
            dias_vida_util,
            fecha_envasado,
            fecha_limite,
            responsable,
            observaciones,
            empresa_id,
        ))
    else:
        conn.execute("""
            UPDATE produccion_lotes_elaborados
            SET estado = CASE WHEN estado = 'disponible' THEN 'cerrado' ELSE estado END,
                observaciones = COALESCE(NULLIF(?, ''), observaciones)
            WHERE linea_produccion_id = ?
        """, (motivo_merma or observaciones, linea_id))

    receta_txt = f"Parte {parte['id']} linea {linea_id}"
    valor_elaboracion = (
        f"{receta_txt} | raciones {linea['cantidad_real'] or linea['cantidad_planificada'] or 0}"
        f" | temp fin {temperatura_elaboracion if temperatura_elaboracion is not None else '-'} C"
    )
    registrar_appcc_control(
        conn,
        empresa_id,
        "ELAB-01",
        valor=valor_elaboracion,
        resultado="ok",
        usuario=responsable,
        observaciones=observaciones or "Control de elaboracion guardado desde produccion",
        origen_tabla="lineas_produccion",
        origen_id=linea_id,
        codigo_operacion=f"elaboracion_linea_{linea_id}",
    )

    metodo_norm = _normalizar_texto(metodo_conservacion)
    if metodo_norm in {"abatido", "frio"}:
        if temp_fin is None:
            resultado_abat = "pendiente"
        elif temp_fin > 10:
            resultado_abat = "no_ok"
        else:
            resultado_abat = "ok"
        registrar_appcc_control(
            conn,
            empresa_id,
            "ABAT-01",
            valor=f"{receta_txt} | {metodo_conservacion} | inicio {temp_inicio if temp_inicio is not None else '-'} C | fin {temp_fin if temp_fin is not None else '-'} C",
            resultado=resultado_abat,
            usuario=responsable,
            observaciones=observaciones,
            accion_correctiva="Revisar enfriamiento/abatimiento" if resultado_abat == "no_ok" else "",
            origen_tabla="lineas_produccion",
            origen_id=linea_id,
            codigo_operacion=f"abatimiento_linea_{linea_id}",
        )

    if metodo_norm == "caliente" or temp_servicio is not None:
        if temp_servicio is None:
            resultado_caliente = "pendiente"
        elif temp_servicio < 65:
            resultado_caliente = "no_ok"
        else:
            resultado_caliente = "ok"
        registrar_appcc_control(
            conn,
            empresa_id,
            "CAL-01",
            valor=f"{receta_txt} | servicio {temp_servicio if temp_servicio is not None else '-'} C",
            resultado=resultado_caliente,
            usuario=responsable,
            observaciones=observaciones,
            accion_correctiva="Regenerar, retirar o desechar" if resultado_caliente == "no_ok" else "",
            origen_tabla="lineas_produccion",
            origen_id=linea_id,
            codigo_operacion=f"caliente_linea_{linea_id}",
        )

    if requiere_regeneracion:
        if temp_regeneracion is None:
            resultado_regen = "pendiente"
        elif temp_regeneracion < 65:
            resultado_regen = "no_ok"
        else:
            resultado_regen = "ok"
        registrar_appcc_control(
            conn,
            empresa_id,
            "REGEN-01",
            valor=f"{receta_txt} | regeneracion {temp_regeneracion if temp_regeneracion is not None else '-'} C | {tiempo_regeneracion if tiempo_regeneracion is not None else '-'} min",
            resultado=resultado_regen,
            usuario=responsable,
            observaciones=observaciones,
            accion_correctiva="Continuar regeneracion o retirar producto" if resultado_regen == "no_ok" else "",
            origen_tabla="lineas_produccion",
            origen_id=linea_id,
            codigo_operacion=f"regeneracion_linea_{linea_id}",
        )

    if cantidad_sobrante > 0 and destino_sobrante in {"merma_buffet", "desecho"}:
        resultado_merma = "ok" if motivo_merma else "no_ok"
        registrar_appcc_control(
            conn,
            empresa_id,
            "MERMA-01",
            valor=f"{receta_txt} | {destino_sobrante} | {cantidad_sobrante:g} {unidad_sobrante}",
            resultado=resultado_merma,
            usuario=responsable,
            observaciones=motivo_merma or "Merma/desecho sin motivo informado",
            accion_correctiva="Completar motivo y revisar aprovechamiento" if resultado_merma == "no_ok" else "",
            origen_tabla="lineas_produccion",
            origen_id=linea_id,
            codigo_operacion=f"merma_linea_{linea_id}",
        )


def _guardar_sobrante_linea(conn, parte, linea, empresa_id, limpiar=False):
    if conn.execute('SELECT 1 FROM produccion_salidas_sobrantes s JOIN produccion_lotes_elaborados l ON l.id=s.lote_id WHERE l.linea_produccion_id=? AND s.empresa_id=? LIMIT 1', (linea['id'], empresa_id)).fetchone():
        return
    linea_id = linea["id"]
    responsable = session.get("user_nombre") or parte["responsable"] or "admin"

    if limpiar:
        destino_sobrante = "sin_sobrante"
        cantidad_sobrante = 0.0
        unidad_sobrante = "raciones"
        motivo_merma = ""
        obs_sobrante = ""
    else:
        destino_sobrante = request.form.get(f"destino_sobrante_{linea_id}") or "merma_buffet"
        cantidad_sobrante = _to_float(request.form.get(f"cantidad_sobrante_{linea_id}"), 0.0)
        unidad_sobrante = (request.form.get(f"unidad_sobrante_{linea_id}") or "raciones").strip()
        motivo_merma = (request.form.get(f"motivo_merma_{linea_id}") or "").strip()
        obs_sobrante = (request.form.get(f"obs_sobrante_{linea_id}") or "").strip()

    control_guardado = conn.execute('SELECT * FROM produccion_controles_appcc WHERE linea_produccion_id = ?', (linea_id,)).fetchone()
    conservacion = dict(control_guardado) if control_guardado else {}
    for campo in ('ubicacion_conservacion', 'metodo_conservacion', 'tipo_envase', 'fecha_envasado', 'fecha_limite_uso'):
        clave = f'{campo}_{linea_id}'
        if clave in request.form:
            conservacion[campo] = request.form.get(clave, '').strip()
    control_guardado = conservacion
    _validar_sobrante(conn, linea, destino_sobrante, cantidad_sobrante, unidad_sobrante,
                     (control_guardado['ubicacion_conservacion'] or '') if control_guardado else '',
                     (control_guardado['metodo_conservacion'] or '') if control_guardado else '',
                     (control_guardado['tipo_envase'] or '') if control_guardado else '',
                     (control_guardado['fecha_envasado'] or '') if control_guardado else '',
                     (control_guardado['fecha_limite_uso'] or '') if control_guardado else '')

    conn.execute("""
        INSERT INTO produccion_controles_appcc (
            parte_id, linea_produccion_id, receta_id, responsable,
            destino_sobrante, cantidad_sobrante, unidad_sobrante, motivo_merma,
            observaciones, empresa_id, actualizado_en
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(linea_produccion_id)
        DO UPDATE SET
            destino_sobrante = excluded.destino_sobrante,
            cantidad_sobrante = excluded.cantidad_sobrante,
            unidad_sobrante = excluded.unidad_sobrante,
            motivo_merma = excluded.motivo_merma,
            observaciones = CASE
                WHEN COALESCE(excluded.observaciones, '') = '' THEN observaciones
                ELSE excluded.observaciones
            END,
            responsable = excluded.responsable,
            empresa_id = excluded.empresa_id,
            actualizado_en = datetime('now')
    """, (
        parte["id"],
        linea_id,
        linea["receta_id"],
        responsable,
        destino_sobrante,
        cantidad_sobrante,
        unidad_sobrante,
        motivo_merma,
        obs_sobrante,
        empresa_id,
    ))

    if destino_sobrante == 'reaprovechamiento' and cantidad_sobrante > 0:
        conn.execute('''
            UPDATE produccion_controles_appcc
            SET ubicacion_conservacion=?, metodo_conservacion=?, tipo_envase=?,
                fecha_envasado=?, fecha_limite_uso=?
            WHERE linea_produccion_id=? AND empresa_id=?
        ''', tuple(conservacion.get(campo, '') for campo in
                   ('ubicacion_conservacion', 'metodo_conservacion', 'tipo_envase', 'fecha_envasado', 'fecha_limite_uso'))
                   + (linea_id, empresa_id))

    if limpiar or cantidad_sobrante <= 0 or destino_sobrante != "reaprovechamiento":
        conn.execute("""
            UPDATE produccion_lotes_elaborados
            SET estado = CASE WHEN estado = 'disponible' THEN 'cerrado' ELSE estado END,
                observaciones = COALESCE(NULLIF(?, ''), observaciones)
            WHERE linea_produccion_id = ?
        """, (motivo_merma or obs_sobrante, linea_id))
    else:
        control = conn.execute("""
            SELECT *
            FROM produccion_controles_appcc
            WHERE linea_produccion_id = ?
        """, (linea_id,)).fetchone()
        fecha_txt = str(parte["fecha"] or datetime.now().strftime("%Y-%m-%d"))[:10].replace("-", "")
        codigo_lote = f"ELAB-{fecha_txt}-{linea_id}"
        metodo = (control["metodo_conservacion"] if control else None) or "abatido"
        tipo_envase = (control["tipo_envase"] if control else None) or "recipiente_tapado"
        dias_vida_util = control["dias_vida_util"] if control and control["dias_vida_util"] is not None else _dias_vida_util_defecto(metodo, tipo_envase)
        fecha_envasado = (control["fecha_envasado"] if control else None) or datetime.now().strftime("%Y-%m-%dT%H:%M")
        fecha_limite = (control["fecha_limite_uso"] if control else None) or _calcular_fecha_limite(fecha_envasado, dias_vida_util)
        conn.execute("""
            INSERT INTO produccion_lotes_elaborados (
                codigo_lote, parte_id, linea_produccion_id, receta_id,
                cantidad, unidad, metodo_conservacion, ubicacion,
                temperatura_inicio, temperatura_fin, tipo_envase, dias_vida_util,
                fecha_elaboracion, fecha_limite_uso, estado, responsable,
                observaciones, empresa_id, fecha
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'disponible', ?, ?, ?, datetime('now'))
            ON CONFLICT(linea_produccion_id)
            DO UPDATE SET
                cantidad = excluded.cantidad,
                unidad = excluded.unidad,
                metodo_conservacion = excluded.metodo_conservacion,
                ubicacion = excluded.ubicacion,
                temperatura_inicio = excluded.temperatura_inicio,
                temperatura_fin = excluded.temperatura_fin,
                tipo_envase = excluded.tipo_envase,
                dias_vida_util = excluded.dias_vida_util,
                fecha_elaboracion = excluded.fecha_elaboracion,
                fecha_limite_uso = excluded.fecha_limite_uso,
                estado = 'disponible',
                responsable = excluded.responsable,
                observaciones = excluded.observaciones,
                empresa_id = excluded.empresa_id,
                fecha = datetime('now')
        """, (
            codigo_lote,
            parte["id"],
            linea_id,
            linea["receta_id"],
            cantidad_sobrante,
            unidad_sobrante,
            metodo,
            (control["ubicacion_conservacion"] if control else None) or "",
            control["temperatura_inicio_conservacion"] if control else None,
            control["temperatura_fin_conservacion"] if control else None,
            tipo_envase,
            dias_vida_util,
            fecha_envasado,
            fecha_limite,
            responsable,
            obs_sobrante or motivo_merma,
            empresa_id,
        ))

    if cantidad_sobrante > 0 and destino_sobrante in {"merma_buffet", "desecho"}:
        registrar_appcc_control(
            conn,
            empresa_id,
            "MERMA-01",
            valor=f"Parte {parte['id']} linea {linea_id} | {destino_sobrante} | {cantidad_sobrante:g} {unidad_sobrante}",
            resultado="ok" if motivo_merma else "no_ok",
            usuario=responsable,
            observaciones=motivo_merma or obs_sobrante or "Merma/desecho sin motivo informado",
            accion_correctiva="Completar motivo y revisar aprovechamiento" if not motivo_merma else "",
            origen_tabla="lineas_produccion",
            origen_id=linea_id,
            codigo_operacion=f"merma_linea_{linea_id}",
        )


def _to_float(valor, defecto=0.0):
    try:
        txt = str(valor if valor is not None else "").strip().replace(",", ".")
        return float(txt) if txt else defecto
    except Exception:
        return defecto


def _normalizar_texto(valor):
    txt = str(valor or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in txt if not unicodedata.combining(c))


def _to_int(valor, defecto=0):
    try:
        txt = str(valor if valor is not None else "").strip()
        return int(float(txt.replace(",", "."))) if txt else defecto
    except Exception:
        return defecto


def _resumen_costes_produccion(conn, empresa_id, fecha_desde, fecha_hasta, servicio="", incluir_centro=False):
    servicio = (servicio or "").strip()
    scope_sql, scope_params = _scope_partes_produccion(conn, "pp", empresa_id)
    params = scope_params + [fecha_desde, fecha_hasta]
    filtro_servicio = ""
    if servicio:
        filtro_servicio = " AND COALESCE(NULLIF(pp.turno, ''), 'Sin servicio') = ?"
        params.append(servicio)
    centro_select = ", COALESCE(s.nombre, pv.nombre, 'Sin centro') AS centro" if incluir_centro else ""
    centro_group = ", b.centro" if incluir_centro else ""
    centro_order = ", centro" if incluir_centro else ""
    centro_join = """
        LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
        LEFT JOIN puntos_venta pv ON pv.id = pp.punto_venta_id
    """ if incluir_centro else ""

    rows = conn.execute(f"""
        WITH base AS (
            SELECT
                pp.id AS parte_id,
                pp.fecha,
                COALESCE(NULLIF(pp.turno, ''), 'Sin servicio') AS servicio,
                pp.estado AS parte_estado,
                lp.id AS linea_id,
                lp.receta_id,
                COALESCE(r.escandallo_fiable, 1) AS escandallo_fiable,
                COALESCE(lp.estado, 'pendiente') AS linea_estado,
                COALESCE(lp.raciones_planificadas, lp.cantidad_real, lp.cantidad_planificada, 0) AS raciones,
                COALESCE(lp.cantidad_real, lp.cantidad_planificada, 1) AS multiplicador
                {centro_select}
            FROM partes_produccion pp
            JOIN lineas_produccion lp ON lp.parte_id = pp.id
            JOIN recetas r ON r.id = lp.receta_id
            {centro_join}
            WHERE {scope_sql}
              AND date(pp.fecha) >= date(?)
              AND date(pp.fecha) <= date(?)
              {filtro_servicio}
        ),
        coste_teorico AS (
            SELECT
                b.linea_id,
                CASE
                    WHEN MAX(COALESCE(b.escandallo_fiable, 1)) = 1
                    THEN SUM(COALESCE(ri.cantidad, 0) * b.multiplicador * COALESCE(p.precio, 0))
                    ELSE 0
                END AS coste
            FROM base b
            LEFT JOIN recetas_ingredientes ri ON ri.receta_id = b.receta_id
            LEFT JOIN productos p ON p.id = ri.producto_id
            GROUP BY b.linea_id
        ),
        coste_real_registrado AS (
            SELECT
                b.linea_id,
                COUNT(pir.id) AS registros,
                SUM(COALESCE(pir.cantidad_real, 0) * COALESCE(p.precio, 0)) AS coste
            FROM base b
            JOIN produccion_ingredientes_reales pir ON pir.linea_produccion_id = b.linea_id
            JOIN productos p ON p.id = pir.producto_id
            LEFT JOIN ingredientes_omitidos io
              ON io.parte_id = b.parte_id
             AND io.receta_ingrediente_id = pir.receta_ingrediente_id
            WHERE io.id IS NULL
              AND COALESCE(pir.cantidad_real, 0) > 0
            GROUP BY b.linea_id
        ),
        coste_real_consumo AS (
            SELECT
                b.linea_id,
                COUNT(cp.id) AS registros,
                SUM(COALESCE(cp.coste_total, COALESCE(cp.cantidad_consumida, 0) * COALESCE(cp.coste_unitario, l.coste_unitario, p.precio, 0))) AS coste
            FROM base b
            JOIN consumo_produccion cp ON cp.linea_produccion_id = b.linea_id
            LEFT JOIN lotes l ON l.id = cp.lote_id
            LEFT JOIN productos p ON p.id = COALESCE(cp.producto_id, l.producto_id)
            GROUP BY b.linea_id
        ),
        coste_real_receta AS (
            SELECT
                b.linea_id,
                CASE
                    WHEN MAX(COALESCE(b.escandallo_fiable, 1)) = 1
                    THEN SUM(COALESCE(ri.cantidad, 0) * b.multiplicador * COALESCE(p.precio, 0))
                    ELSE 0
                END AS coste
            FROM base b
            LEFT JOIN recetas_ingredientes ri ON ri.receta_id = b.receta_id
            LEFT JOIN productos p ON p.id = ri.producto_id
            LEFT JOIN ingredientes_omitidos io
              ON io.parte_id = b.parte_id
             AND io.receta_ingrediente_id = ri.id
            WHERE io.id IS NULL
            GROUP BY b.linea_id
        ),
        lineas_coste AS (
            SELECT
                b.*,
                COALESCE(ct.coste, 0) AS coste_estimado,
                CASE
                    WHEN COALESCE(cc.registros, 0) > 0 THEN COALESCE(cc.coste, 0)
                    WHEN COALESCE(cr.registros, 0) > 0 THEN COALESCE(cr.coste, 0)
                    ELSE COALESCE(crr.coste, ct.coste, 0)
                END AS coste_real,
                COALESCE(pc.destino_sobrante, 'sin_sobrante') AS destino_sobrante,
                COALESCE(pc.cantidad_sobrante, 0) AS cantidad_sobrante
            FROM base b
            LEFT JOIN coste_teorico ct ON ct.linea_id = b.linea_id
            LEFT JOIN coste_real_consumo cc ON cc.linea_id = b.linea_id
            LEFT JOIN coste_real_registrado cr ON cr.linea_id = b.linea_id
            LEFT JOIN coste_real_receta crr ON crr.linea_id = b.linea_id
            LEFT JOIN produccion_controles_appcc pc ON pc.linea_produccion_id = b.linea_id
        )
        SELECT
            b.fecha,
            b.servicio
            {", b.centro" if incluir_centro else ""},
            COUNT(DISTINCT b.parte_id) AS partes,
            COUNT(b.linea_id) AS recetas,
            COALESCE(SUM(b.raciones), 0) AS raciones,
            COALESCE(SUM(b.coste_estimado), 0) AS coste_estimado,
            COALESCE(SUM(b.coste_real), 0) AS coste_real,
            COALESCE(SUM(
                CASE
                    WHEN b.destino_sobrante IN ('merma_buffet', 'desecho') THEN b.cantidad_sobrante
                    ELSE 0
                END
            ), 0) AS merma_cantidad,
            COALESCE(SUM(
                CASE
                    WHEN b.destino_sobrante = 'reaprovechamiento' THEN b.cantidad_sobrante
                    ELSE 0
                END
            ), 0) AS reaprovechamiento_cantidad,
            COALESCE(SUM(
                CASE
                    WHEN b.destino_sobrante IN ('merma_buffet', 'desecho') AND b.raciones > 0
                    THEN b.coste_real * (MIN(b.cantidad_sobrante, b.raciones) / b.raciones)
                    ELSE 0
                END
            ), 0) AS valor_merma,
            COALESCE(SUM(
                CASE
                    WHEN b.destino_sobrante = 'reaprovechamiento' AND b.raciones > 0
                    THEN b.coste_real * (MIN(b.cantidad_sobrante, b.raciones) / b.raciones)
                    ELSE 0
                END
            ), 0) AS valor_reaprovechado,
            SUM(CASE WHEN b.linea_estado = 'pendiente' THEN 1 ELSE 0 END) AS pendientes,
            SUM(CASE WHEN b.linea_estado = 'en_proceso' THEN 1 ELSE 0 END) AS en_proceso,
            SUM(CASE WHEN b.linea_estado = 'completado' THEN 1 ELSE 0 END) AS completadas
        FROM lineas_coste b
        GROUP BY b.fecha, b.servicio{centro_group}
        ORDER BY b.fecha, b.servicio{centro_order}
    """, params).fetchall()

    resultado = []
    keys = ["fecha", "servicio"] + (["centro"] if incluir_centro else [])
    por_clave = {}
    for row in rows:
        item = dict(row)
        item["coste_extra"] = 0.0
        item["coste_total_real"] = _to_float(item.get("coste_real"), 0.0)
        item["coste_imputado_servicio"] = max(
            item["coste_total_real"] - _to_float(item.get("valor_reaprovechado"), 0.0),
            0.0,
        )
        item["desviacion"] = item["coste_total_real"] - _to_float(item.get("coste_estimado"), 0.0)
        clave = tuple(item[k] for k in keys)
        por_clave[clave] = item
        resultado.append(item)

    extra_params = list(params)
    extras = conn.execute(f"""
        SELECT
            pp.fecha,
            COALESCE(NULLIF(pp.turno, ''), 'Sin servicio') AS servicio
            {", COALESCE(s.nombre, pv.nombre, 'Sin centro') AS centro" if incluir_centro else ""},
            COALESCE(SUM(COALESCE(ce.cantidad, 0) * COALESCE(p.precio, 0)), 0) AS coste_extra
        FROM partes_produccion pp
        JOIN consumo_extra ce ON ce.parte_id = pp.id
        JOIN productos p ON p.id = ce.producto_id
        {centro_join}
        WHERE {scope_sql}
          AND date(pp.fecha) >= date(?)
          AND date(pp.fecha) <= date(?)
          {filtro_servicio}
        GROUP BY pp.fecha, servicio{", centro" if incluir_centro else ""}
    """, extra_params).fetchall()

    for row in extras:
        clave = tuple(row[k] for k in keys)
        item = por_clave.get(clave)
        if not item:
            item = {k: row[k] for k in keys}
            item.update({
                "partes": 0,
                "recetas": 0,
                "raciones": 0.0,
                "coste_estimado": 0.0,
                "coste_real": 0.0,
                "merma_cantidad": 0.0,
                "reaprovechamiento_cantidad": 0.0,
                "valor_merma": 0.0,
                "valor_reaprovechado": 0.0,
                "pendientes": 0,
                "en_proceso": 0,
                "completadas": 0,
                "coste_extra": 0.0,
            })
            resultado.append(item)
            por_clave[clave] = item
        item["coste_extra"] = _to_float(row["coste_extra"], 0.0)
        item["coste_total_real"] = _to_float(item.get("coste_real"), 0.0) + item["coste_extra"]
        item["coste_imputado_servicio"] = max(
            item["coste_total_real"] - _to_float(item.get("valor_reaprovechado"), 0.0),
            0.0,
        )
        item["desviacion"] = item["coste_total_real"] - _to_float(item.get("coste_estimado"), 0.0)

    return resultado


def _totales_resumen_costes(rows):
    return {
        "partes": sum(_to_int(row.get("partes"), 0) for row in rows),
        "recetas": sum(_to_int(row.get("recetas"), 0) for row in rows),
        "raciones": sum(_to_float(row.get("raciones"), 0.0) for row in rows),
        "coste_estimado": sum(_to_float(row.get("coste_estimado"), 0.0) for row in rows),
        "coste_real": sum(_to_float(row.get("coste_real"), 0.0) for row in rows),
        "coste_extra": sum(_to_float(row.get("coste_extra"), 0.0) for row in rows),
        "coste_total_real": sum(_to_float(row.get("coste_total_real"), 0.0) for row in rows),
        "coste_imputado_servicio": sum(_to_float(row.get("coste_imputado_servicio"), 0.0) for row in rows),
        "merma_cantidad": sum(_to_float(row.get("merma_cantidad"), 0.0) for row in rows),
        "reaprovechamiento_cantidad": sum(_to_float(row.get("reaprovechamiento_cantidad"), 0.0) for row in rows),
        "valor_merma": sum(_to_float(row.get("valor_merma"), 0.0) for row in rows),
        "valor_reaprovechado": sum(_to_float(row.get("valor_reaprovechado"), 0.0) for row in rows),
        "desviacion": sum(_to_float(row.get("desviacion"), 0.0) for row in rows),
    }


def _detalle_costes_platos_produccion(conn, empresa_id, fecha_desde, fecha_hasta, servicio=""):
    servicio = (servicio or "").strip()
    scope_sql, scope_params = _scope_partes_produccion(conn, "pp", empresa_id)
    params = scope_params + [fecha_desde, fecha_hasta]
    filtro_servicio = ""
    if servicio:
        filtro_servicio = " AND COALESCE(NULLIF(pp.turno, ''), 'Sin servicio') = ?"
        params.append(servicio)
    rows = conn.execute(f"""
        WITH base AS (
            SELECT
                pp.id AS parte_id,
                pp.fecha,
                COALESCE(NULLIF(pp.turno, ''), 'Sin servicio') AS servicio,
                lp.id AS linea_id,
                lp.receta_id,
                r.nombre AS plato,
                COALESCE(r.escandallo_fiable, 1) AS escandallo_fiable,
                COALESCE(s.nombre, pv.nombre, 'Sin centro') AS centro,
                COALESCE(lp.raciones_planificadas, lp.cantidad_real, lp.cantidad_planificada, 0) AS raciones,
                COALESCE(lp.cantidad_real, lp.cantidad_planificada, 1) AS multiplicador
            FROM partes_produccion pp
            JOIN lineas_produccion lp ON lp.parte_id = pp.id
            JOIN recetas r ON r.id = lp.receta_id
            LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
            LEFT JOIN puntos_venta pv ON pv.id = pp.punto_venta_id
            WHERE {scope_sql}
              AND date(pp.fecha) >= date(?)
              AND date(pp.fecha) <= date(?)
              {filtro_servicio}
        ),
        coste_teorico AS (
            SELECT
                b.linea_id,
                CASE
                    WHEN MAX(COALESCE(b.escandallo_fiable, 1)) = 1
                    THEN SUM(COALESCE(ri.cantidad, 0) * b.multiplicador * COALESCE(p.precio, 0))
                    ELSE 0
                END AS coste
            FROM base b
            LEFT JOIN recetas_ingredientes ri ON ri.receta_id = b.receta_id
            LEFT JOIN productos p ON p.id = ri.producto_id
            GROUP BY b.linea_id
        ),
        coste_real_registrado AS (
            SELECT
                b.linea_id,
                COUNT(pir.id) AS registros,
                SUM(COALESCE(pir.cantidad_real, 0) * COALESCE(p.precio, 0)) AS coste
            FROM base b
            JOIN produccion_ingredientes_reales pir ON pir.linea_produccion_id = b.linea_id
            JOIN productos p ON p.id = pir.producto_id
            LEFT JOIN ingredientes_omitidos io
              ON io.parte_id = b.parte_id
             AND io.receta_ingrediente_id = pir.receta_ingrediente_id
            WHERE io.id IS NULL
              AND COALESCE(pir.cantidad_real, 0) > 0
            GROUP BY b.linea_id
        ),
        coste_real_consumo AS (
            SELECT
                b.linea_id,
                COUNT(cp.id) AS registros,
                SUM(COALESCE(cp.coste_total, COALESCE(cp.cantidad_consumida, 0) * COALESCE(cp.coste_unitario, l.coste_unitario, p.precio, 0))) AS coste
            FROM base b
            JOIN consumo_produccion cp ON cp.linea_produccion_id = b.linea_id
            LEFT JOIN lotes l ON l.id = cp.lote_id
            LEFT JOIN productos p ON p.id = COALESCE(cp.producto_id, l.producto_id)
            GROUP BY b.linea_id
        ),
        coste_real_receta AS (
            SELECT
                b.linea_id,
                CASE
                    WHEN MAX(COALESCE(b.escandallo_fiable, 1)) = 1
                    THEN SUM(COALESCE(ri.cantidad, 0) * b.multiplicador * COALESCE(p.precio, 0))
                    ELSE 0
                END AS coste
            FROM base b
            LEFT JOIN recetas_ingredientes ri ON ri.receta_id = b.receta_id
            LEFT JOIN productos p ON p.id = ri.producto_id
            LEFT JOIN ingredientes_omitidos io
              ON io.parte_id = b.parte_id
             AND io.receta_ingrediente_id = ri.id
            WHERE io.id IS NULL
            GROUP BY b.linea_id
        ),
        lineas_coste AS (
            SELECT
                b.*,
                COALESCE(ct.coste, 0) AS coste_estimado,
                CASE
                    WHEN COALESCE(cc.registros, 0) > 0 THEN COALESCE(cc.coste, 0)
                    WHEN COALESCE(cr.registros, 0) > 0 THEN COALESCE(cr.coste, 0)
                    ELSE COALESCE(crr.coste, ct.coste, 0)
                END AS coste_real,
                COALESCE(pc.destino_sobrante, 'sin_sobrante') AS destino_sobrante,
                COALESCE(pc.cantidad_sobrante, 0) AS cantidad_sobrante
            FROM base b
            LEFT JOIN coste_teorico ct ON ct.linea_id = b.linea_id
            LEFT JOIN coste_real_consumo cc ON cc.linea_id = b.linea_id
            LEFT JOIN coste_real_registrado cr ON cr.linea_id = b.linea_id
            LEFT JOIN coste_real_receta crr ON crr.linea_id = b.linea_id
            LEFT JOIN produccion_controles_appcc pc ON pc.linea_produccion_id = b.linea_id
        )
        SELECT
            fecha,
            servicio,
            plato,
            centro,
            COUNT(*) AS veces,
            ROUND(SUM(raciones), 1) AS raciones,
            ROUND(SUM(coste_estimado), 2) AS coste_estimado,
            ROUND(SUM(coste_real), 2) AS coste_real,
            ROUND(SUM(
                CASE
                    WHEN destino_sobrante IN ('merma_buffet', 'desecho') AND raciones > 0
                    THEN coste_real * (MIN(cantidad_sobrante, raciones) / raciones)
                    ELSE 0
                END
            ), 2) AS valor_merma,
            ROUND(SUM(
                CASE
                    WHEN destino_sobrante = 'reaprovechamiento' AND raciones > 0
                    THEN coste_real * (MIN(cantidad_sobrante, raciones) / raciones)
                    ELSE 0
                END
            ), 2) AS valor_reaprovechado,
            ROUND(SUM(
                CASE
                    WHEN destino_sobrante IN ('merma_buffet', 'desecho') THEN cantidad_sobrante
                    ELSE 0
                END
            ), 1) AS merma_cantidad,
            ROUND(SUM(
                CASE
                    WHEN destino_sobrante = 'reaprovechamiento' THEN cantidad_sobrante
                    ELSE 0
                END
            ), 1) AS reaprovechamiento_cantidad
        FROM lineas_coste
        GROUP BY fecha, servicio, receta_id, plato, centro
        ORDER BY fecha DESC, valor_merma DESC, coste_real DESC, plato
        LIMIT 200
    """, params).fetchall()

    resultado = []
    for row in rows:
        item = dict(row)
        item["coste_imputado_servicio"] = max(
            _to_float(item.get("coste_real"), 0.0) - _to_float(item.get("valor_reaprovechado"), 0.0),
            0.0,
        )
        item["coste_racion"] = (
            item["coste_imputado_servicio"] / _to_float(item.get("raciones"), 0.0)
            if _to_float(item.get("raciones"), 0.0) > 0
            else 0.0
        )
        resultado.append(item)
    return resultado


def _servicio_desde_tipo_menu(nombre):
    txt = _normalizar_texto(nombre)
    if "desay" in txt:
        return "desayuno"
    if "almuerzo" in txt or "comida" in txt:
        return "comida"
    if "cena" in txt:
        return "cena"
    if "snack" in txt or "bar" in txt:
        return "bebidas_snacks"
    return txt.replace(" ", "_") or "servicio"


def _servicios_por_regimen_produccion(regimen):
    regimen = regimen or "solo_alojamiento"
    if regimen == "alojamiento_desayuno":
        return {"desayuno"}
    if regimen == "media_pension":
        return {"desayuno", "cena"}
    if regimen == "pension_completa":
        return {"desayuno", "comida", "cena"}
    if regimen == "todo_incluido":
        return {"desayuno", "comida", "cena", "bebidas_snacks"}
    return set()


def _servicio_aplica_fecha_produccion(servicio, entrada, salida, fecha_obj):
    if not entrada or not salida or salida <= entrada:
        return False
    if servicio == "desayuno":
        return entrada < fecha_obj <= salida
    return entrada <= fecha_obj < salida


def _parse_fecha(valor):
    try:
        return datetime.strptime(str(valor or "")[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _pax_previstos_servicio(conn, empresa_id, fecha_txt, tipo_menu_nombre):
    fecha_obj = _parse_fecha(fecha_txt)
    if not fecha_obj:
        return 0
    servicio = _servicio_desde_tipo_menu(tipo_menu_nombre)
    centro_sql, centro_params = _filtro_centro_tabla(conn, "reservas_recepcion", "r")
    reservas = conn.execute(f"""
        SELECT fecha_entrada, fecha_salida, regimen, adultos, menores, estado
        FROM reservas_recepcion r
        WHERE empresa_id = ?
          AND COALESCE(estado, 'pendiente') NOT IN ('cancelada', 'cancelado', 'no_show')
          AND date(fecha_entrada) <= date(?)
          AND date(fecha_salida) >= date(?)
          {centro_sql}
    """, [empresa_id, fecha_txt, fecha_txt] + centro_params).fetchall()

    total = 0
    for reserva in reservas:
        entrada = _parse_fecha(reserva["fecha_entrada"])
        salida = _parse_fecha(reserva["fecha_salida"])
        if servicio not in _servicios_por_regimen_produccion(reserva["regimen"]):
            continue
        if not _servicio_aplica_fecha_produccion(servicio, entrada, salida, fecha_obj):
            continue
        total += max(_to_int(reserva["adultos"], 0), 0) + max(_to_int(reserva["menores"], 0), 0)
    return total


def _clientes_servicio_terminal(conn, empresa_id, fecha_txt, servicio):
    fecha_obj = _parse_fecha(fecha_txt)
    if not fecha_obj:
        return {"clientes": [], "pasantes": [], "totales": {"previstos": 0, "entrados_reserva": 0, "faltan": 0, "pasantes": 0, "entrados": 0}}
    if not _table_exists(conn, "reservas_recepcion"):
        return {"clientes": [], "pasantes": [], "totales": {"previstos": 0, "entrados_reserva": 0, "faltan": 0, "pasantes": 0, "entrados": 0}}

    centro_reserva_sql, centro_reserva_params = _filtro_centro_tabla(conn, "reservas_recepcion", "r")
    reservas = conn.execute(f"""
        SELECT r.id, r.codigo, r.titular_nombre, r.regimen, r.adultos, r.menores,
               r.fecha_entrada, r.fecha_salida, r.estado,
               h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON h.id = r.habitacion_id
        WHERE r.empresa_id = ?
          AND COALESCE(r.estado, 'pendiente') NOT IN ('cancelada', 'cancelado', 'no_show')
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) >= date(?)
          {centro_reserva_sql}
        ORDER BY COALESCE(h.numero, h.nombre, ''), r.titular_nombre, r.codigo
    """, [empresa_id, fecha_txt, fecha_txt] + centro_reserva_params).fetchall()

    entradas_por_reserva = {}
    pasantes = []
    if _table_exists(conn, "tpv_buffet_entradas"):
        centro_entrada_sql, centro_entrada_params = _filtro_centro_tabla(conn, "tpv_buffet_entradas", "")
        entradas = conn.execute(f"""
            SELECT reserva_id,
                   MAX(habitacion) AS habitacion,
                   MAX(titular) AS titular,
                   MAX(regimen) AS regimen,
                   COALESCE(SUM(COALESCE(adultos, 0) + COALESCE(menores, 0)), 0) AS total
            FROM tpv_buffet_entradas
            WHERE empresa_id = ?
              AND fecha = ?
              AND servicio = ?
              {centro_entrada_sql}
            GROUP BY reserva_id, habitacion, titular, regimen
        """, [empresa_id, fecha_txt, servicio] + centro_entrada_params).fetchall()
        for row in entradas:
            reserva_id = row["reserva_id"] or 0
            total = _to_int(row["total"], 0)
            if reserva_id:
                entradas_por_reserva[reserva_id] = total
            else:
                pasantes.append({
                    "habitacion": row["habitacion"] or "-",
                    "titular": row["titular"] or "Pasante",
                    "regimen": row["regimen"] or "sin_reserva",
                    "pax": total,
                    "motivo": "Sin reserva",
                })

    clientes = []
    total_previstos = 0
    total_entrados_reserva = 0
    total_faltan = 0
    total_pasantes = sum(_to_int(p["pax"], 0) for p in pasantes)

    for reserva in reservas:
        entrada = _parse_fecha(reserva["fecha_entrada"])
        salida = _parse_fecha(reserva["fecha_salida"])
        if servicio not in _servicios_por_regimen_produccion(reserva["regimen"]):
            continue
        if not _servicio_aplica_fecha_produccion(servicio, entrada, salida, fecha_obj):
            continue

        pax = max(_to_int(reserva["adultos"], 0) + _to_int(reserva["menores"], 0), 1)
        entrados_total = _to_int(entradas_por_reserva.get(reserva["id"], 0), 0)
        entrados_con_derecho = min(entrados_total, pax)
        extra = max(entrados_total - pax, 0)
        faltan = max(pax - entrados_con_derecho, 0)
        habitacion = reserva["habitacion_numero"] or reserva["habitacion_nombre"] or "-"

        if extra:
            pasantes.append({
                "habitacion": habitacion,
                "titular": reserva["titular_nombre"] or reserva["codigo"] or "Reserva",
                "regimen": reserva["regimen"] or "",
                "pax": extra,
                "motivo": "Extra sobre reserva",
            })
            total_pasantes += extra

        clientes.append({
            "reserva_id": reserva["id"],
            "codigo": reserva["codigo"],
            "habitacion": habitacion,
            "titular": reserva["titular_nombre"] or reserva["codigo"] or "Reserva",
            "regimen": reserva["regimen"] or "",
            "pax": pax,
            "entrados": entrados_con_derecho,
            "faltan": faltan,
            "estado": "completo" if faltan == 0 else ("parcial" if entrados_con_derecho else "pendiente"),
        })
        total_previstos += pax
        total_entrados_reserva += entrados_con_derecho
        total_faltan += faltan

    clientes.sort(key=lambda item: (item["faltan"] <= 0, item["habitacion"], item["titular"]))
    return {
        "clientes": clientes,
        "pasantes": pasantes,
        "totales": {
            "previstos": total_previstos,
            "entrados_reserva": total_entrados_reserva,
            "faltan": total_faltan,
            "pasantes": total_pasantes,
            "entrados": total_entrados_reserva + total_pasantes,
        },
    }


def _resumen_servicios_terminal(conn, empresa_id, fecha_txt, partes):
    servicios = {}
    labels_por_servicio = {
        "desayuno": "Desayuno",
        "comida": "Almuerzo",
        "cena": "Cena",
        "bebidas_snacks": "Snacks / bebidas",
    }
    for parte in partes:
        servicio_key = _servicio_desde_tipo_menu(parte["turno"] or "")
        label = parte["turno"] or servicio_key.replace("_", " ").title()
        if servicio_key not in servicios:
            servicios[servicio_key] = {
                "key": servicio_key,
                "label": label,
                "previstos": 0,
                "entrados": 0,
                "entrados_reserva": 0,
                "pasantes": 0,
                "faltan": 0,
                "clientes": [],
                "pasantes_detalle": [],
            }
        servicios[servicio_key]["previstos"] += _to_float(parte["pax_previstos"], 0)

    for servicio_key, label in labels_por_servicio.items():
        if servicio_key in servicios:
            continue
        control = _clientes_servicio_terminal(conn, empresa_id, fecha_txt, servicio_key)
        totales = control["totales"]
        if not any(_to_float(totales.get(k), 0) for k in ("previstos", "entrados", "faltan", "pasantes")):
            continue
        servicios[servicio_key] = {
            "key": servicio_key,
            "label": label,
            "previstos": 0,
            "entrados": 0,
            "entrados_reserva": 0,
            "pasantes": 0,
            "faltan": 0,
            "clientes": [],
            "pasantes_detalle": [],
        }

    for item in servicios.values():
        control = _clientes_servicio_terminal(conn, empresa_id, fecha_txt, item["key"])
        item["clientes"] = control["clientes"]
        item["pasantes_detalle"] = control["pasantes"]
        item["previstos"] = max(_to_float(item["previstos"], 0), _to_float(control["totales"]["previstos"], 0))
        item["entrados"] = _to_float(control["totales"]["entrados"], 0)
        item["entrados_reserva"] = _to_float(control["totales"]["entrados_reserva"], 0)
        item["pasantes"] = _to_float(control["totales"]["pasantes"], 0)
        item["faltan"] = max(_to_float(control["totales"]["faltan"], 0), 0)
    return list(servicios.values())


def _regla_buffet(tipo_menu_nombre, subdepartamento_nombre, seccion, receta_nombre):
    texto = _normalizar_texto(
        f"{tipo_menu_nombre} {subdepartamento_nombre} {seccion} {receta_nombre}"
    )
    nombre = _normalizar_texto(receta_nombre)

    bolleria = (
        "napolitana", "croissant", "ensaimada", "magdalena", "donut", "berlina",
        "bolleria", "bolleria", "palmera", "caracola", "caña", "cana",
        "pastelito", "hojaldre", "pan chocolate", "pain au chocolat",
    )
    if any(x in nombre for x in bolleria):
        return {
            "estacion": "Desayuno - bolleria/panes",
            "pct": 0.16,
            "rango": "12-18%",
            "nota": "Bolleria de desayuno: ambiente o vitrina seca, nunca linea caliente.",
        }

    quesos_fiambres = (
        "queso", "edam", "gouda", "havarti", "cheddar", "chedar", "emmental",
        "emental", "roquefort", "fresco", "fiambre", "embutido", "jamon york",
        "jamon serrano", "mortadela", "salami", "chorizo", "pavo lonchas",
    )
    if any(x in nombre for x in quesos_fiambres) and not any(x in nombre for x in ("tarta", "quesadilla", "pizza", "salsa", "gratin")):
        return {
            "estacion": "Frios - quesos y fiambres",
            "pct": 0.11,
            "rango": "8-14%",
            "nota": "Quesos y fiambres siempre en frio, con pinza propia y control de temperatura.",
        }

    if "quesadilla" in nombre:
        return {
            "estacion": "Calientes - snack",
            "pct": 0.22,
            "rango": "18-25%",
            "nota": "Elaboracion caliente/snack, no confundir con tabla de quesos.",
        }

    if any(x in texto for x in ("postre", "pasteler", "tarta", "flan", "mousse", "bizcocho", "helado", "fruta")):
        return {
            "estacion": "Postres",
            "pct": 0.18,
            "rango": "15-20%",
            "nota": "Formato mini o racion reducida.",
        }

    if any(x in texto for x in ("frio", "fiambre", "queso", "ensalada", "crudite", "encurtido", "pate", "terrina")):
        return {
            "estacion": "Frios",
            "pct": 0.125,
            "rango": "10-15%",
            "nota": "Alta variedad de buffet frio.",
        }

    if any(x in texto for x in ("sopa", "crema", "consome", "caldo")):
        return {
            "estacion": "Sopas y cremas",
            "pct": 0.18,
            "rango": "15-20%",
            "nota": "Racion reducida por variedad.",
        }

    proteinas = (
        "carne", "pollo", "ternera", "cerdo", "cordero", "pavo", "conejo",
        "pescado", "atun", "merluza", "salmon", "dorada", "lubina", "bacalao",
        "calamar", "sepia", "gamba", "marisco", "mejillon", "proteina",
        "hamburg", "lomo", "costilla", "chuleta", "entrecot",
    )
    if any(x in texto for x in proteinas):
        return {
            "estacion": "Calientes - proteina",
            "pct": 0.30,
            "rango": "25-30%",
            "nota": "Proteina principal del buffet.",
        }

    veg_guarnicion = (
        "vegano", "vegetar", "verdura", "guarnicion", "arroz", "pasta",
        "patata", "papa", "legumbre", "lenteja", "garbanzo", "alubia",
        "judia", "wok", "menestra",
    )
    if any(x in texto for x in veg_guarnicion):
        return {
            "estacion": "Veganos/guarniciones",
            "pct": 0.18,
            "rango": "15-20%",
            "nota": "Acompanamiento o plato vegetal.",
        }

    if "desay" in texto:
        return {
            "estacion": "Desayuno",
            "pct": 0.15,
            "rango": "10-20%",
            "nota": "Variedad alta de desayuno.",
        }

    return {
        "estacion": "Buffet general",
        "pct": 0.20,
        "rango": "15-20%",
        "nota": "Regla generica de buffet.",
    }


def _calcular_linea_buffet(pax_previstos, tipo_menu_nombre, subdepartamento_nombre, seccion, receta):
    raciones_base = _to_float(receta["raciones_base"] if "raciones_base" in receta.keys() else 4, 4.0) or 4.0
    pax = max(_to_float(pax_previstos, 0.0), 0.0)
    regla = _regla_buffet(tipo_menu_nombre, subdepartamento_nombre, seccion, receta["nombre"])
    raciones = max(math.ceil(pax * regla["pct"]), 1) if pax > 0 else 0
    fm = raciones / raciones_base
    return {
        "cantidad_planificada": round(fm, 4),
        "raciones_planificadas": float(raciones),
        "pax_previstos": pax,
        "estacion_buffet": regla["estacion"],
        "porcentaje_prevision": regla["pct"],
        "regla_calculo": f"{regla['estacion']} {regla['rango']} sobre {pax:g} pax. {regla['nota']}",
    }


def _insertar_linea_produccion_buffet(
    conn,
    parte_id,
    receta,
    pax_previstos,
    tipo_menu_nombre,
    subdepartamento_nombre="",
    seccion="",
):
    calculo = _calcular_linea_buffet(
        pax_previstos,
        tipo_menu_nombre,
        subdepartamento_nombre,
        seccion,
        receta,
    )
    conn.execute("""
        INSERT INTO lineas_produccion (
            parte_id, receta_id, cantidad_planificada, raciones_planificadas,
            pax_previstos, estacion_buffet, porcentaje_prevision, regla_calculo,
            estado
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pendiente')
    """, (
        parte_id,
        receta["id"],
        calculo["cantidad_planificada"],
        calculo["raciones_planificadas"],
        calculo["pax_previstos"],
        calculo["estacion_buffet"],
        calculo["porcentaje_prevision"],
        calculo["regla_calculo"],
    ))
    return calculo


def _cantidad_prevista_ingrediente(linea, cantidad_base):
    multiplicador = _to_float(
        linea["cantidad_real"] if "cantidad_real" in linea.keys() and linea["cantidad_real"] is not None else linea["cantidad_planificada"],
        1.0,
    )
    return _to_float(cantidad_base, 0.0) * multiplicador


def _separar_alergenos(valor):
    texto = str(valor or "").strip()
    if not texto:
        return []
    for separador in ("\n", ";", "|", "/"):
        texto = texto.replace(separador, ",")
    return [item.strip() for item in texto.split(",") if item.strip()]


def _unir_alergenos(items):
    orden = [
        "Cereales con gluten", "Crustáceos", "Huevo", "Pescado", "Cacahuete",
        "Soja", "Leche", "Frutos con cáscara", "Apio", "Mostaza", "Sésamo",
        "Sulfitos", "Altramuces", "Moluscos",
    ]
    normalizados = {}
    for item in items:
        for txt in _separar_alergenos(item):
            clave = _normalizar_texto(txt)
            normalizados[clave] = txt

    resultado = []
    for nombre in orden:
        clave = _normalizar_texto(nombre)
        if clave in normalizados:
            resultado.append(normalizados.pop(clave))
    resultado.extend(sorted(normalizados.values(), key=lambda x: _normalizar_texto(x)))
    return resultado


def _inferir_alergenos_por_texto(*valores):
    return detectar_alergenos(*valores)
    texto = _normalizar_texto(" ".join(str(v or "") for v in valores))
    reglas = [
        ("Cereales con gluten", ("gluten", "trigo", "harina", "pan", "pasta", "macarron", "espagueti", "tallarin", "fideo", "pizza", "hojaldre", "bizcocho", "galleta", "croissant", "nugget", "rebozad")),
        ("Huevo", ("huevo", "tortilla", "mahonesa", "mayonesa", "ali oli", "alioli", "flan", "crema pastelera")),
        ("Leche", ("leche", "queso", "gouda", "edam", "mozzarella", "mozarella", "nata", "mantequilla", "yogur", "yogurt", "lacteo", "bechamel", "chocolate con leche")),
        ("Pescado", ("pescado", "atun", "tilapia", "merluza", "salmon", "bacalao", "dorada", "lubina", "anchoa", "sardina", "bonito")),
        ("Crustáceos", ("gamba", "langostino", "langosta", "cangrejo", "camaron", "crustace")),
        ("Moluscos", ("mejillon", "almeja", "calamar", "sepia", "pulpo", "molusco")),
        ("Soja", ("soja", "soy", "salsa de soja", "tofu")),
        ("Mostaza", ("mostaza",)),
        ("Sésamo", ("sesamo", "sésamo", "tahini")),
        ("Apio", ("apio",)),
        ("Frutos con cáscara", ("almendra", "avellana", "nuez", "pistacho", "anacardo", "pacana", "macadamia")),
        ("Cacahuete", ("cacahuete", "mani")),
        ("Sulfitos", ("sulfito", "vino", "vinagre", "encurtido", "aceituna", "pepinillo", "alcaparra")),
        ("Altramuces", ("altramuz", "altramuces")),
    ]
    encontrados = []
    for alergeno, claves in reglas:
        if any(clave in texto for clave in claves):
            encontrados.append(alergeno)
    return _unir_alergenos(encontrados)


def _row_get(row, campo, default=""):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(campo, default)
    try:
        if campo in row.keys():
            return row[campo]
    except Exception:
        pass
    return default


IDIOMAS_TARJETA_BUFFET = {
    "es": "ES",
    "en": "EN",
    "fr": "FR",
    "de": "DE",
    "it": "IT",
}

ALERGENOS_TARJETA = {
    "cereales con gluten": ("gluten.svg", "Gluten"),
    "crustaceos": ("crustaceos.svg", "Crustaceos"),
    "huevos": ("huevos.svg", "Huevos"),
    "pescado": ("pescado.svg", "Pescado"),
    "cacahuetes": ("cacahuetes.svg", "Cacahuetes"),
    "soja": ("soja.svg", "Soja"),
    "leche": ("leche.svg", "Leche"),
    "frutos con cascara": ("frutos-cascara.svg", "Frutos con cascara"),
    "apio": ("apio.svg", "Apio"),
    "mostaza": ("mostaza.svg", "Mostaza"),
    "sesamo": ("sesamo.svg", "Sesamo"),
    "sulfitos": ("sulfitos.svg", "Sulfitos"),
    "altramuces": ("altramuces.svg", "Altramuces"),
    "moluscos": ("moluscos.svg", "Moluscos"),
}
ALERGENOS_CANONICOS = {
    _normalizar_texto(nombre): nombre
    for nombre in ALERGENOS_UE
}
ALERGENOS_CANONICOS.update({
    "gluten": "Cereales con gluten",
    "cereal con gluten": "Cereales con gluten",
    "cereales gluten": "Cereales con gluten",
    "crustaceo": "Crustaceos",
    "huevo": "Huevos",
    "cacahuete": "Cacahuetes",
    "frutos secos": "Frutos con cascara",
    "frutos de cascara": "Frutos con cascara",
    "lacteos": "Leche",
    "lacteo": "Leche",
    "sulfito": "Sulfitos",
    "altramuz": "Altramuces",
})


def _solo_alergenos_canonicos(items):
    resultado = []
    for item in items:
        canonico = ALERGENOS_CANONICOS.get(_normalizar_texto(item))
        if canonico:
            resultado.append(canonico)
    return resultado


def _idiomas_tarjeta_desde_request():
    raw = request.args.get("idiomas") or "es,en,de"
    idiomas = []
    for item in raw.replace(";", ",").split(","):
        codigo = item.strip().lower()
        if codigo in IDIOMAS_TARJETA_BUFFET and codigo not in idiomas:
            idiomas.append(codigo)
    return idiomas or ["es", "en"]


def _traducciones_tarjeta(linea, idiomas):
    nombres = []
    nombre_es = (_row_get(linea, "receta_nombre") or "").strip()
    for codigo in idiomas:
        campo = "receta_nombre" if codigo == "es" else f"nombre_{codigo}"
        texto = (_row_get(linea, campo) or "").strip()
        if not texto and codigo == "es":
            texto = nombre_es
        elif not texto and nombre_es:
            texto = traducir_nombre_receta(nombre_es, codigo)
        if texto:
            nombres.append({
                "idioma": codigo,
                "label": IDIOMAS_TARJETA_BUFFET[codigo],
                "texto": texto,
            })
    if not nombres and nombre_es:
        nombres.append({"idioma": "es", "label": "ES", "texto": nombre_es})
    return nombres


def _badges_alergenos_tarjeta(alergenos):
    badges = []
    usados = set()
    for alergeno in _separar_alergenos(alergenos):
        canonico = ALERGENOS_CANONICOS.get(_normalizar_texto(alergeno))
        if not canonico:
            continue
        clave = _normalizar_texto(canonico)
        simbolo, label = ALERGENOS_TARJETA[clave]
        clave_badge = _normalizar_texto(label)
        if clave_badge in usados:
            continue
        usados.add(clave_badge)
        badges.append({"simbolo": simbolo, "label": label, "canonico": canonico})
    return badges


def _preparar_tarjetas_publicas(lineas, idiomas):
    for idx, item in enumerate(lineas, start=1):
        item["tarjeta_nombres"] = _traducciones_tarjeta(item, idiomas)
        item["tarjeta_alergenos"] = _badges_alergenos_tarjeta(item.get("alergenos_mostrar") or item.get("alergenos_operativos"))
        estacion = (item.get("estacion_buffet") or "").strip()
        item["tarjeta_codigo"] = estacion if estacion else f"PS-{idx:02d}"
    return lineas


def _alergenos_linea_operativa(linea, ingredientes=None):
    ingredientes = ingredientes or []
    declarados = []
    textos_para_inferir = []
    if linea:
        declarados.extend(_solo_alergenos_canonicos(_separar_alergenos(_row_get(linea, "alergenos_mostrar"))))
        declarados.extend(_solo_alergenos_canonicos(_separar_alergenos(_row_get(linea, "alergenos"))))
        for campo in ("receta_nombre", "descripcion"):
            textos_para_inferir.append(_row_get(linea, campo))
    for ing in ingredientes:
        declarados.extend(_solo_alergenos_canonicos(_separar_alergenos(_row_get(ing, "alergenos"))))
        textos_para_inferir.append(_row_get(ing, "nombre"))
    inferidos = detectar_alergenos(*textos_para_inferir)
    return ", ".join(_unir_alergenos([*declarados, *inferidos]))


def _avisos_higiene_alergenos(alergenos, contaminacion_cruzada=False):
    lista = _separar_alergenos(alergenos)
    if not lista:
        return []
    avisos = [
        "Lavar manos y cambiar guantes antes de manipular.",
        "Limpiar y desinfectar mesa, tabla, cuchillos, pinzas y recipientes antes de empezar.",
        "Usar utensilios limpios o exclusivos y mantener el producto tapado e identificado.",
    ]
    normalizados = {_normalizar_texto(a) for a in lista}
    if "cereales con gluten" in normalizados:
        avisos.append("Evitar migas, harina, pan rallado y aceite/freidora compartidos con elaboraciones sin gluten.")
    if normalizados & {"crustaceos", "pescado", "moluscos"}:
        avisos.append("Separar tablas y cuchillos de pescado/marisco; limpiar fregadero y superficies tras el manipulado.")
    if normalizados & {"frutos con cascara", "cacahuetes", "sesamo"}:
        avisos.append("Evitar restos secos en bandejas, cucharones, molinillos, toppings y zonas de emplatado.")
    if contaminacion_cruzada:
        avisos.append("Hay elaboraciones cercanas con alergenos distintos: limpiar entre recetas y separar produccion/servicio.")
    return avisos


def _anotar_contaminacion_cruzada(lineas):
    grupos = {}
    for item in lineas:
        clave = (
            item.get("parte_id"),
            _normalizar_texto(item.get("subdepartamento_nombre") or ""),
            _normalizar_texto(item.get("estacion_buffet") or ""),
        )
        grupos.setdefault(clave, []).append(item)

    for grupo in grupos.values():
        union_grupo = set()
        por_linea = {}
        for item in grupo:
            alergenos = _alergenos_linea_operativa(item)
            item["alergenos_lista"] = _separar_alergenos(alergenos)
            item["alergenos_operativos"] = alergenos
            claves = {_normalizar_texto(a) for a in item["alergenos_lista"]}
            por_linea[item.get("id")] = claves
            union_grupo.update(claves)
        for item in grupo:
            claves = por_linea.get(item.get("id"), set())
            contaminacion = bool(claves and (union_grupo - claves))
            item["contaminacion_cruzada"] = contaminacion
            item["avisos_higiene"] = _avisos_higiene_alergenos(item.get("alergenos_operativos"), contaminacion)
    return lineas


def _preparar_lineas_tarjetas(conn, lineas_rows):
    lineas = []
    for linea in lineas_rows:
        parte_id = linea["parte_id"] if "parte_id" in linea.keys() else None
        ingredientes_rows = []
        origen_ingredientes = "Produccion planificada"

        if parte_id:
            ingredientes_rows = conn.execute("""
                SELECT p.nombre, p.unidad, pir.cantidad_real AS cantidad,
                       p.alergenos, p.alergenos_validado, 1 AS es_real
                FROM produccion_ingredientes_reales pir
                JOIN productos p ON p.id = pir.producto_id
                LEFT JOIN ingredientes_omitidos io
                  ON io.receta_ingrediente_id = pir.receta_ingrediente_id
                 AND io.parte_id = ?
                WHERE pir.linea_produccion_id = ?
                  AND io.id IS NULL
                  AND COALESCE(pir.cantidad_real, 0) > 0
                ORDER BY p.nombre
            """, (parte_id, linea["id"])).fetchall()

        if ingredientes_rows:
            origen_ingredientes = "Produccion real"
        else:
            ingredientes_rows = conn.execute("""
                SELECT p.nombre, p.unidad, ri.cantidad,
                       p.alergenos, p.alergenos_validado, 0 AS es_real
                FROM recetas_ingredientes ri
                JOIN productos p ON p.id = ri.producto_id
                LEFT JOIN ingredientes_omitidos io
                  ON io.receta_ingrediente_id = ri.id
                 AND io.parte_id = ?
                WHERE ri.receta_id = ?
                  AND io.id IS NULL
                ORDER BY p.nombre
            """, (parte_id, linea["receta_id"])).fetchall()

        ingredientes = []
        vistos = set()
        alergenos_productos = []
        pendiente_productos = False
        for ing in ingredientes_rows:
            clave = _normalizar_texto(ing["nombre"])
            if not clave or clave in vistos:
                continue
            vistos.add(clave)

            alergenos_producto = _solo_alergenos_canonicos(
                _separar_alergenos(ing["alergenos"] if "alergenos" in ing.keys() else "")
            )
            validado = _to_int(ing["alergenos_validado"] if "alergenos_validado" in ing.keys() else 0, 0)
            if alergenos_producto:
                alergenos_productos.extend(alergenos_producto)
                if not validado:
                    pendiente_productos = True
            else:
                inferidos_producto = _inferir_alergenos_por_texto(ing["nombre"])
                if inferidos_producto:
                    alergenos_productos.extend(inferidos_producto)
                    pendiente_productos = True
                elif not validado:
                    pendiente_productos = True

            ingredientes.append({
                "nombre": ing["nombre"],
                "unidad": ing["unidad"],
                "cantidad": ing["cantidad"],
                "alergenos": ", ".join(alergenos_producto),
                "alergenos_validado": validado,
                "es_real": _to_int(ing["es_real"] if "es_real" in ing.keys() else 0, 0),
            })

        item = dict(linea)
        item["origen_ingredientes"] = origen_ingredientes
        alergenos_desde_productos = _unir_alergenos(alergenos_productos)
        if alergenos_desde_productos:
            item["alergenos_mostrar"] = ", ".join(alergenos_desde_productos)
            item["alergenos_pendiente"] = pendiente_productos
            item["alergenos_fuente"] = "productos de produccion"
        else:
            inferidos = _inferir_alergenos_por_texto(
                " ".join(i["nombre"] for i in ingredientes),
            )
            item["alergenos_mostrar"] = ", ".join(inferidos)
            item["alergenos_pendiente"] = True
            item["alergenos_fuente"] = "pendiente productos"

        item["ingredientes"] = ingredientes
        item["ingredientes_texto"] = ", ".join(i["nombre"] for i in ingredientes)
        lineas.append(item)
    return _anotar_contaminacion_cruzada(lineas)


def _crear_partes_buffet_desde_menu(
    conn,
    menu,
    empresa_id,
    fecha_servicio=None,
    filtro_subdepartamento_id=None,
    pax_override=0,
    observaciones="",
):
    fecha_servicio = fecha_servicio or menu["fecha"]
    tipo_menu = menu["tipo_nombre"]
    centro_id = menu["centro_id"] if "centro_id" in menu.keys() and menu["centro_id"] else centro_activo_id()
    pax_previstos = pax_override or _pax_previstos_servicio(conn, empresa_id, fecha_servicio, tipo_menu)
    secciones = conn.execute("""
        SELECT ms.*, s.id AS subdepartamento_id, s.nombre AS subdepartamento_nombre
        FROM menu_subdepartamentos ms
        JOIN subdepartamentos s ON ms.subdepartamento_id = s.id
        WHERE ms.menu_diario_id = ?
          AND (? IS NULL OR ms.subdepartamento_id = ?)
        ORDER BY s.nombre, ms.orden, ms.seccion
    """, (menu["id"], filtro_subdepartamento_id, filtro_subdepartamento_id)).fetchall()

    partes_creados = 0
    lineas_creadas = 0

    secciones_por_subdepto = {}
    for seccion in secciones:
        clave = seccion["subdepartamento_id"]
        grupo = secciones_por_subdepto.setdefault(clave, {
            "subdepartamento_id": seccion["subdepartamento_id"],
            "subdepartamento_nombre": seccion["subdepartamento_nombre"],
            "secciones": [],
        })
        grupo["secciones"].append(seccion)

    for grupo in secciones_por_subdepto.values():
        cursor = conn.cursor()
        existente = conn.execute("""
            SELECT id, estado
            FROM partes_produccion
            WHERE empresa_id = ?
              AND date(fecha) = date(?)
              AND COALESCE(turno, '') = COALESCE(?, '')
              AND menu_diario_id = ?
              AND subdepartamento_id = ?
              AND COALESCE(centro_id, 0) = COALESCE(?, 0)
            ORDER BY id DESC
            LIMIT 1
        """, (
            empresa_id,
            fecha_servicio,
            tipo_menu,
            menu["id"],
            grupo["subdepartamento_id"],
            centro_id,
        )).fetchone()

        if existente:
            parte_id = existente["id"]
            if (existente["estado"] or "planificado") != "planificado":
                continue

            registros = conn.execute("""
                SELECT
                    (SELECT COUNT(*)
                     FROM consumo_produccion cp
                     JOIN lineas_produccion lp ON lp.id = cp.linea_produccion_id
                     WHERE lp.parte_id = ?) +
                    (SELECT COUNT(*)
                     FROM produccion_ingredientes_reales pir
                     JOIN lineas_produccion lp ON lp.id = pir.linea_produccion_id
                     WHERE lp.parte_id = ?) +
                    (SELECT COUNT(*) FROM consumo_extra WHERE parte_id = ?)
                    AS total
            """, (parte_id, parte_id, parte_id)).fetchone()
            if _to_int(registros["total"] if registros else 0, 0) > 0:
                continue

            conn.execute("""
                DELETE FROM produccion_ingredientes_reales
                WHERE linea_produccion_id IN (
                    SELECT id FROM lineas_produccion WHERE parte_id = ?
                )
            """, (parte_id,))
            conn.execute("DELETE FROM ingredientes_omitidos WHERE parte_id = ?", (parte_id,))
            conn.execute("DELETE FROM lineas_produccion WHERE parte_id = ?", (parte_id,))
            conn.execute("""
                UPDATE partes_produccion
                SET responsable = ?,
                    observaciones = ?,
                    pax_previstos = ?,
                    regla_calculo = ?,
                    estado = 'planificado',
                    centro_id = ?
                WHERE id = ?
            """, (
                session.get('user_nombre'),
                observaciones or f"Desde menu {menu['id']}",
                pax_previstos,
                f"Buffet calculado con {pax_previstos} pax previstos",
                centro_id,
                parte_id,
            ))
            partes_creados += 1
        else:
            cursor.execute("""
                INSERT INTO partes_produccion 
                (fecha, turno, responsable, menu_diario_id, subdepartamento_id, 
                 observaciones, estado, empresa_id, pax_previstos, regla_calculo, centro_id)
                VALUES (?, ?, ?, ?, ?, ?, 'planificado', ?, ?, ?, ?)
            """, (
                fecha_servicio,
                tipo_menu,
                session.get('user_nombre'),
                menu["id"],
                grupo["subdepartamento_id"],
                observaciones or f"Desde menu {menu['id']}",
                empresa_id,
                pax_previstos,
                f"Buffet calculado con {pax_previstos} pax previstos",
                centro_id,
            ))
            parte_id = cursor.lastrowid
            partes_creados += 1

        for seccion in grupo["secciones"]:
            platos = conn.execute("""
                SELECT mp.receta_id, r.id, r.nombre, COALESCE(r.raciones_base, 4) AS raciones_base
                FROM menu_platos mp
                JOIN recetas r ON r.id = mp.receta_id
                WHERE mp.menu_subdepartamento_id = ?
                ORDER BY mp.orden, r.nombre
            """, (seccion["id"],)).fetchall()

            for plato in platos:
                _insertar_linea_produccion_buffet(
                    conn,
                    parte_id,
                    plato,
                    pax_previstos,
                    tipo_menu,
                    grupo["subdepartamento_nombre"],
                    seccion["seccion"],
                )
                lineas_creadas += 1

    return partes_creados, lineas_creadas


def _producto_requiere_desinfeccion(producto):
    if not producto:
        return False
    if int(producto["requiere_desinfeccion"] or 0) == 1:
        return True
    texto = f"{producto['familia'] or ''} {producto['nombre'] or ''}".lower()
    claves = ("fruta", "verdura", "vegetal", "lechuga", "tomate", "pepino", "pimiento")
    return any(clave in texto for clave in claves)


FAMILIAS_SIN_ALERTA_DESCONGELADO = {
    "helados",
    "bebidas",
    "bebidas_agua",
    "bebidas_refrescos",
    "bebidas_licores",
    "bebidas_premium",
}
FAMILIAS_DESCONGELADO_LARGO = {"carnes", "pescados", "mariscos", "moluscos", "cefalopodos"}
CLAVES_CONGELADO = ("congel", "cong", "freezer", "uco")
CLAVES_SIN_ALERTA_DESCONGELADO = ("helado", "ice cream", "polo", "sorbete", "granizado")
CLAVES_DESCONGELADO_LARGO = (
    "carne", "carnes", "pollo", "pavo", "cerdo", "ternera", "vacuno", "cordero",
    "pescado", "pescados", "merluza", "atun", "at?n", "bacalao", "salmon", "salm?n",
    "lubina", "dorada", "rape", "rodaballo", "lenguado", "gallo", "corvina",
    "marisco", "mariscos", "gamba", "gambas", "langostino", "calamar", "sepia", "pulpo",
)
CLAVES_DESCONGELADO_MEDIO = (
    "hojaldre", "masa", "pan", "bolleria", "pasteleria", "tarta", "bizcocho",
    "croqueta", "canelon", "lasa", "empanadilla", "rollito", "precocinado", "gratinado",
)


def _producto_es_congelado(producto):
    if not producto:
        return False
    conservacion = _normalizar_texto(producto["tipo_conservacion"] if "tipo_conservacion" in producto.keys() else "")
    texto = _normalizar_texto(
        f"{producto['familia'] if 'familia' in producto.keys() else ''} "
        f"{producto['nombre'] if 'nombre' in producto.keys() else ''}"
    )
    return any(clave in conservacion for clave in CLAVES_CONGELADO) or any(clave in texto for clave in CLAVES_CONGELADO)


def _producto_se_sirve_congelado(producto):
    familia = _normalizar_texto(producto["familia"] if producto and "familia" in producto.keys() else "")
    texto = _normalizar_texto(
        f"{producto['familia'] if producto and 'familia' in producto.keys() else ''} "
        f"{producto['nombre'] if producto and 'nombre' in producto.keys() else ''}"
    )
    return familia in FAMILIAS_SIN_ALERTA_DESCONGELADO or any(clave in texto for clave in CLAVES_SIN_ALERTA_DESCONGELADO)


def _producto_requiere_alerta_descongelado(producto):
    return _horas_descongelado(producto) > 0


def _horas_descongelado(producto):
    if not _producto_es_congelado(producto) or _producto_se_sirve_congelado(producto):
        return 0
    horas = _to_int(producto["tiempo_descongelado_horas"] if "tiempo_descongelado_horas" in producto.keys() else 0, 0)
    # Plazo operativo minimo acordado; conservar configuraciones mas largas.
    return max(horas, 72)

def _stock_producto(conn, producto_id, empresa_id=None):
    empresa_id = empresa_id or session.get("user_empresa_id", 1)
    centro_id = centro_activo_id()
    lotes_join, centro_lotes_sql, centro_lotes_params = _filtro_lotes_centro(conn, 'l', centro_id)
    lotes = conn.execute(f"""
        SELECT COUNT(*) AS registros,
               COALESCE(SUM(CASE
                   WHEN l.estado = 'activo'
                     AND COALESCE(l.estado_descongelado, 'pendiente') <> 'no_apto'
                     AND (NULLIF(l.fecha_caducidad, '') IS NULL OR date(l.fecha_caducidad) >= date('now'))
                   THEN MAX(COALESCE(l.cantidad_actual, 0), 0) ELSE 0 END), 0) AS total
        FROM lotes l
        {lotes_join}
        WHERE l.producto_id = ?
          AND l.empresa_id = ?
          {centro_lotes_sql}
    """, [producto_id, empresa_id] + centro_lotes_params).fetchone()
    stock_lotes = _to_float(lotes["total"] if lotes else 0)
    if lotes and lotes['registros'] > 0:
        return stock_lotes

    centro_stock_sql = ""
    centro_stock_params = []
    if centro_id and "centro_id" in _columnas_tabla(conn.cursor(), "almacenes"):
        centro_stock_sql = " AND a.centro_id = ?"
        centro_stock_params = [centro_id]
    stock = conn.execute(f"""
        SELECT COALESCE(SUM(sa.cantidad), 0) AS total
        FROM stock_almacen sa
        JOIN almacenes a ON a.id = sa.almacen_id
        WHERE sa.producto_id = ?
          AND a.empresa_id = ?
          AND COALESCE(a.activo, 1) = 1
          {centro_stock_sql}
    """, [producto_id, empresa_id] + centro_stock_params).fetchone()
    return _to_float(stock["total"] if stock else 0)


def _lotes_disponibles_para_consumo(conn, producto, empresa_id, centro_id=None):
    requiere_descongelado = _producto_requiere_alerta_descongelado(producto)
    lotes_join, lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, "l", centro_id)
    producto_id = producto["producto_id"] if "producto_id" in producto.keys() else producto["id"]
    if requiere_descongelado:
        lotes = conn.execute(f"""
            SELECT l.*
            FROM lotes l
            {lotes_join}
            WHERE l.producto_id = ?
              AND l.empresa_id = ?
              AND l.estado = 'activo'
              AND COALESCE(l.cantidad_actual, 0) > 0
              AND COALESCE(l.estado_descongelado, 'pendiente') = 'apto'
              AND (NULLIF(l.fecha_caducidad, '') IS NULL OR date(l.fecha_caducidad) >= date('now'))
              {lotes_centro_sql}
            ORDER BY fecha_caducidad IS NULL, fecha_caducidad, fecha_ultimo_control, id
        """, [producto_id, empresa_id] + lotes_centro_params).fetchall()
        return lotes, requiere_descongelado

    lotes = conn.execute(f"""
        SELECT l.*
        FROM lotes l
        {lotes_join}
        WHERE l.producto_id = ?
          AND l.empresa_id = ?
          AND l.estado = 'activo'
          AND COALESCE(l.cantidad_actual, 0) > 0
          AND COALESCE(l.estado_descongelado, 'pendiente') NOT IN ('no_apto', 'en_curso')
          AND (NULLIF(l.fecha_caducidad, '') IS NULL OR date(l.fecha_caducidad) >= date('now'))
          {lotes_centro_sql}
        ORDER BY fecha_caducidad IS NULL, fecha_caducidad, id
    """, [producto_id, empresa_id] + lotes_centro_params).fetchall()
    return lotes, requiere_descongelado


def _descontar_lote_consumido(conn, lote, cantidad, empresa_id):
    if cantidad <= 0 or cantidad > _to_float(lote["cantidad_actual"], 0.0):
        raise ValueError("Cantidad de consumo fuera del saldo disponible del lote")
    estado_descongelado = (lote["estado_descongelado"] if "estado_descongelado" in lote.keys() else "") or ""
    nuevo_estado_descongelado = estado_descongelado
    cantidad_actual = _to_float(lote["cantidad_actual"], 0.0)
    cantidad_restante = max(0.0, cantidad_actual - cantidad)
    nuevo_estado = lote["estado"] if "estado" in lote.keys() else "activo"

    if cantidad_restante <= 0.0001:
        cantidad_restante = 0.0
        nuevo_estado = "consumido"
        if estado_descongelado in ("apto", "en_curso"):
            nuevo_estado_descongelado = "consumido"

    actualizado = conn.execute("""
        UPDATE lotes
        SET cantidad_actual = ?,
            estado = ?,
            estado_descongelado = ?,
            ubicacion_actual = CASE WHEN ? = 'consumido' THEN 'produccion' ELSE ubicacion_actual END,
            notas_trazabilidad = TRIM(COALESCE(notas_trazabilidad, '') || ' | Consumido en produccion')
        WHERE id = ? AND empresa_id = ?
    """, (
        cantidad_restante,
        nuevo_estado,
        nuevo_estado_descongelado,
        nuevo_estado,
        lote["id"],
        empresa_id,
    ))
    if actualizado.rowcount != 1:
        raise ValueError("Lote no disponible para la empresa activa")
    if lote["almacen_id"]:
        saldo = conn.execute("""
            SELECT cantidad FROM stock_almacen
            WHERE producto_id = ? AND almacen_id = ?
        """, (lote["producto_id"], lote["almacen_id"])).fetchone()
        if saldo is not None:
            if _to_float(saldo["cantidad"], 0.0) < cantidad:
                raise ValueError("Saldo de almacen insuficiente; revisar conciliacion de lotes")
            conn.execute("""
                UPDATE stock_almacen SET cantidad = cantidad - ?
                WHERE producto_id = ? AND almacen_id = ?
            """, (cantidad, lote["producto_id"], lote["almacen_id"]))
            return
        # Solo reconstruir desde lotes si todavia no existe saldo agregado.
        conn.execute("""
            INSERT INTO stock_almacen (producto_id, almacen_id, cantidad)
            SELECT ?, ?, COALESCE(SUM(cantidad_actual), 0)
            FROM lotes WHERE producto_id = ? AND almacen_id = ? AND empresa_id = ?
            HAVING NOT EXISTS (
                SELECT 1 FROM stock_almacen WHERE producto_id = ? AND almacen_id = ?
            )
        """, (lote["producto_id"], lote["almacen_id"], lote["producto_id"],
              lote["almacen_id"], empresa_id, lote["producto_id"], lote["almacen_id"]))


def _pendiente_compra_producto(conn, producto_id, empresa_id, fecha_necesidad=None):
    centro_id = centro_activo_id()
    filtros = []
    params_extra = []
    columnas_pedidos = set(_columnas_tabla(conn.cursor(), "pedidos")) if _table_exists(conn, "pedidos") else set()
    if fecha_necesidad:
        if 'fecha_entrega' not in columnas_pedidos:
            return 0.0
        filtros.append("date(p.fecha_entrega) <= date(?)")
        params_extra.append(fecha_necesidad)
    if centro_id:
        if "centro_id" in columnas_pedidos:
            filtros.append("p.centro_id = ?")
            params_extra.append(centro_id)
        else:
            ambitos = []
            dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
            if dep_ids and "departamento_id" in columnas_pedidos:
                ambitos.append(f"p.departamento_id IN ({','.join('?' for _ in dep_ids)})")
                params_extra.extend(dep_ids)
            pv_ids = ids_puntos_venta_ambito(conn, empresa_id, centro_id)
            if pv_ids and "punto_venta_id" in columnas_pedidos:
                ambitos.append(f"p.punto_venta_id IN ({','.join('?' for _ in pv_ids)})")
                params_extra.extend(pv_ids)
            if ambitos:
                filtros.append("(" + " OR ".join(ambitos) + ")")
            else:
                filtros.append("1 = 0")
    filtro_centro = " AND " + " AND ".join(filtros) if filtros else ""
    row = conn.execute(f"""
        SELECT COALESCE(SUM(
            CASE
                WHEN COALESCE(lp.cantidad_solicitada, 0) > COALESCE(lp.cantidad_recibida, 0)
                THEN COALESCE(lp.cantidad_solicitada, 0) - COALESCE(lp.cantidad_recibida, 0)
                ELSE 0
            END
        ), 0) AS total
        FROM lineas_pedido lp
        JOIN pedidos p ON p.id = lp.pedido_id
        WHERE lp.producto_id = ?
          AND p.empresa_id = ?
          AND COALESCE(p.estado, 'borrador') IN ('enviado', 'recibido_parcial')
          {filtro_centro}
    """, [producto_id, empresa_id] + params_extra).fetchone()
    return _to_float(row["total"] if row else 0)


def _lineas_planificadas(conn, empresa_id, fecha_desde, fecha_hasta):
    centro_partes_sql, centro_partes_params = _filtro_centro_tabla(conn, "partes_produccion", "pp")
    centro_ps_sql, centro_ps_params = _filtro_centro_tabla(conn, "planificacion_servicios", "ps")
    centro_md_sql, centro_md_params = _filtro_centro_tabla(conn, "menus_diarios", "md")
    centro_pp_not_exists_sql, centro_pp_not_exists_params = _filtro_centro_tabla(conn, "partes_produccion", "pp")

    partes_rows = conn.execute(f"""
        SELECT pp.id AS parte_id, pp.fecha, pp.turno, pp.menu_diario_id, pp.hora AS hora_produccion,
               lp.id AS linea_id, lp.receta_id,
               COALESCE(lp.cantidad_planificada, 1) AS multiplicador,
               COALESCE(lp.raciones_planificadas, COALESCE(lp.cantidad_planificada, 1) * COALESCE(r.raciones_base, 4)) AS raciones_planificadas,
               COALESCE(lp.pax_previstos, 0) AS pax_previstos,
               lp.estacion_buffet, lp.regla_calculo,
               r.nombre AS receta_nombre, COALESCE(r.raciones_base, 4) AS raciones_base,
               COALESCE(r.escandallo_fiable, 1) AS escandallo_fiable,
               s.departamento_id AS departamento_id,
               COALESCE(s.nombre, '') AS subdepartamento_nombre,
               COALESCE(mp_menu.seccion, '') AS seccion,
               'parte' AS origen
        FROM partes_produccion pp
        JOIN lineas_produccion lp ON lp.parte_id = pp.id
        JOIN recetas r ON r.id = lp.receta_id
        LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
        LEFT JOIN (
            SELECT md.id AS menu_diario_id, ms.subdepartamento_id, mp.receta_id, ms.seccion
            FROM menus_diarios md
            JOIN menu_subdepartamentos ms ON ms.menu_diario_id = md.id
            JOIN menu_platos mp ON mp.menu_subdepartamento_id = ms.id
        ) mp_menu
          ON mp_menu.menu_diario_id = pp.menu_diario_id
         AND mp_menu.subdepartamento_id = pp.subdepartamento_id
         AND mp_menu.receta_id = lp.receta_id
        WHERE pp.empresa_id = ?
          AND date(pp.fecha) BETWEEN date(?) AND date(?)
          {centro_partes_sql}
        ORDER BY pp.fecha, pp.turno, r.nombre
    """, [empresa_id, fecha_desde, fecha_hasta] + centro_partes_params).fetchall()

    lineas = [dict(row) for row in partes_rows]

    plan_rows = conn.execute(f"""
        SELECT NULL AS parte_id, ps.fecha, t.nombre AS turno, md.id AS menu_diario_id,
               NULL AS linea_id, mp.receta_id,
               r.nombre AS receta_nombre, COALESCE(r.raciones_base, 4) AS raciones_base,
               COALESCE(r.escandallo_fiable, 1) AS escandallo_fiable,
               s.departamento_id AS departamento_id,
               COALESCE(s.nombre, '') AS subdepartamento_nombre,
               COALESCE(ms.seccion, '') AS seccion,
               COALESCE(md.modo_servicio, 'buffet') AS modo_servicio,
               'planificacion' AS origen
        FROM planificacion_servicios ps
        JOIN menus_diarios md ON md.id = ps.menu_diario_id
        JOIN tipos_menu t ON t.id = ps.tipo_menu_id
        JOIN menu_subdepartamentos ms ON ms.menu_diario_id = md.id
        JOIN menu_platos mp ON mp.menu_subdepartamento_id = ms.id
        JOIN recetas r ON r.id = mp.receta_id
        LEFT JOIN subdepartamentos s ON s.id = ms.subdepartamento_id
        WHERE ps.empresa_id = ?
          AND date(ps.fecha) BETWEEN date(?) AND date(?)
          {centro_ps_sql}
          AND (ps.subdepartamento_id IS NULL OR ps.subdepartamento_id = ms.subdepartamento_id)
          AND NOT EXISTS (
              SELECT 1
              FROM partes_produccion pp
              WHERE pp.menu_diario_id = md.id
                AND pp.empresa_id = ps.empresa_id
                AND date(pp.fecha) = date(ps.fecha)
                AND (ps.subdepartamento_id IS NULL OR pp.subdepartamento_id = ps.subdepartamento_id)
                {centro_pp_not_exists_sql}
          )
        ORDER BY ps.fecha, t.nombre, s.nombre, r.nombre
    """, [empresa_id, fecha_desde, fecha_hasta] + centro_ps_params + centro_pp_not_exists_params).fetchall()

    menu_rows = conn.execute(f"""
        SELECT NULL AS parte_id, md.fecha, t.nombre AS turno, md.id AS menu_diario_id,
               NULL AS linea_id, mp.receta_id,
               r.nombre AS receta_nombre, COALESCE(r.raciones_base, 4) AS raciones_base,
               COALESCE(r.escandallo_fiable, 1) AS escandallo_fiable,
               s.departamento_id AS departamento_id,
               COALESCE(s.nombre, '') AS subdepartamento_nombre,
               COALESCE(ms.seccion, '') AS seccion,
               COALESCE(md.modo_servicio, 'buffet') AS modo_servicio,
               'menu' AS origen
        FROM menus_diarios md
        JOIN tipos_menu t ON t.id = md.tipo_menu_id
        JOIN menu_subdepartamentos ms ON ms.menu_diario_id = md.id
        JOIN menu_platos mp ON mp.menu_subdepartamento_id = ms.id
        JOIN recetas r ON r.id = mp.receta_id
        LEFT JOIN subdepartamentos s ON s.id = ms.subdepartamento_id
        WHERE md.empresa_id = ?
          AND date(md.fecha) BETWEEN date(?) AND date(?)
          {centro_md_sql}
          AND NOT EXISTS (
              SELECT 1
              FROM planificacion_servicios ps
              WHERE ps.menu_diario_id = md.id
                AND ps.empresa_id = md.empresa_id
                AND date(ps.fecha) = date(md.fecha)
                {centro_ps_sql}
          )
          AND NOT EXISTS (
              SELECT 1
              FROM partes_produccion pp
              WHERE pp.menu_diario_id = md.id
                AND pp.empresa_id = md.empresa_id
                AND date(pp.fecha) = date(md.fecha)
                {centro_pp_not_exists_sql}
          )
        ORDER BY md.fecha, t.nombre, s.nombre, r.nombre
    """, (
        [empresa_id, fecha_desde, fecha_hasta]
        + centro_md_params
        + centro_ps_params
        + centro_pp_not_exists_params
    )).fetchall()

    for row in list(plan_rows) + list(menu_rows):
        item = dict(row)
        pax = _pax_previstos_servicio(conn, empresa_id, item["fecha"], item["turno"])
        receta = {"nombre": item["receta_nombre"], "raciones_base": item["raciones_base"]}
        calculo = _calcular_linea_buffet(
            pax,
            item["turno"],
            item["subdepartamento_nombre"],
            item["seccion"],
            receta,
        )
        item["multiplicador"] = calculo["cantidad_planificada"]
        item.update(calculo)
        lineas.append(item)

    return lineas


def _auditar_coherencia_menu_produccion(conn, empresa_id, fecha_desde, fecha_hasta):
    centro_pp_sql, centro_pp_params = _filtro_centro_tabla(conn, "partes_produccion", "pp")
    centro_md_sql, centro_md_params = _filtro_centro_tabla(conn, "menus_diarios", "md")

    produccion_sin_menu = conn.execute(f"""
        SELECT pp.fecha, COALESCE(pp.turno, 'Sin servicio') AS servicio,
               COALESCE(s.nombre, pv.nombre, 'Sin centro') AS centro,
               r.nombre AS receta, pp.id AS parte_id
        FROM partes_produccion pp
        JOIN lineas_produccion lp ON lp.parte_id = pp.id
        JOIN recetas r ON r.id = lp.receta_id
        LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
        LEFT JOIN puntos_venta pv ON pv.id = pp.punto_venta_id
        WHERE pp.empresa_id = ?
          AND date(pp.fecha) BETWEEN date(?) AND date(?)
          {centro_pp_sql}
          AND (
              pp.menu_diario_id IS NULL
              OR NOT EXISTS (
                  SELECT 1
                  FROM menu_subdepartamentos ms
                  JOIN menu_platos mp ON mp.menu_subdepartamento_id = ms.id
                  WHERE ms.menu_diario_id = pp.menu_diario_id
                    AND (pp.subdepartamento_id IS NULL OR ms.subdepartamento_id = pp.subdepartamento_id)
                    AND mp.receta_id = lp.receta_id
              )
          )
        ORDER BY pp.fecha DESC, servicio, centro, receta
        LIMIT 80
    """, [empresa_id, fecha_desde, fecha_hasta] + centro_pp_params).fetchall()

    menus_sin_produccion = conn.execute(f"""
        SELECT md.fecha, t.nombre AS servicio,
               COALESCE(s.nombre, 'Sin centro') AS centro,
               r.nombre AS receta, md.id AS menu_diario_id
        FROM menus_diarios md
        JOIN tipos_menu t ON t.id = md.tipo_menu_id
        JOIN menu_subdepartamentos ms ON ms.menu_diario_id = md.id
        JOIN menu_platos mp ON mp.menu_subdepartamento_id = ms.id
        JOIN recetas r ON r.id = mp.receta_id
        LEFT JOIN subdepartamentos s ON s.id = ms.subdepartamento_id
        WHERE md.empresa_id = ?
          AND date(md.fecha) BETWEEN date(?) AND date(?)
          {centro_md_sql}
          AND NOT EXISTS (
              SELECT 1
              FROM partes_produccion pp
              JOIN lineas_produccion lp ON lp.parte_id = pp.id
              WHERE pp.empresa_id = md.empresa_id
                AND pp.menu_diario_id = md.id
                AND date(pp.fecha) = date(md.fecha)
                AND pp.subdepartamento_id = ms.subdepartamento_id
                AND lp.receta_id = mp.receta_id
          )
        ORDER BY md.fecha, servicio, centro, receta
        LIMIT 80
    """, [empresa_id, fecha_desde, fecha_hasta] + centro_md_params).fetchall()

    resumen = {
        "produccion_sin_menu": len(produccion_sin_menu),
        "menus_sin_produccion": len(menus_sin_produccion),
    }
    resumen["estado"] = "ok" if not resumen["produccion_sin_menu"] and not resumen["menus_sin_produccion"] else "revisar"
    return {
        "resumen": resumen,
        "produccion_sin_menu": [dict(row) for row in produccion_sin_menu],
        "menus_sin_produccion": [dict(row) for row in menus_sin_produccion],
    }


def _inicio_preparacion_produccion(fecha, horas_descongelado=0, hora_produccion=None):
    dia = _parse_fecha(fecha)
    if not dia:
        raise ValueError('Fecha de produccion no valida')
    hora = datetime.strptime('08:00', '%H:%M').time()
    if hora_produccion:
        try:
            hora = datetime.strptime(str(hora_produccion).strip()[:5], '%H:%M').time()
        except ValueError:
            raise ValueError('Hora prevista de produccion no valida')
    return datetime.combine(dia, hora) - timedelta(hours=max(_to_float(horas_descongelado, 0), 0))


def _calcular_necesidades_planificacion(conn, empresa_id, fecha_desde, fecha_hasta):
    lineas = _lineas_planificadas(conn, empresa_id, fecha_desde, fecha_hasta)
    coherencia = _auditar_coherencia_menu_produccion(conn, empresa_id, fecha_desde, fecha_hasta)
    productos = {}
    recetas_sin_ingredientes = {}
    ingredientes_sin_cantidad = []
    alertas_descongelado = []
    desinfecciones = {}

    for linea in lineas:
        multiplicador = max(_to_float(linea["multiplicador"], 0.0), 0.0)
        if multiplicador == 0:
            continue
        raciones_base = _to_float(linea["raciones_base"], 4.0) or 4.0
        if _to_int(linea.get("escandallo_fiable"), 1) != 1:
            ingredientes_sin_cantidad.append({
                "receta": linea["receta_nombre"],
                "producto": "Escandallo no fiable",
                "fecha": linea["fecha"],
            })
            continue
        ingredientes = conn.execute("""
            SELECT ri.id, ri.cantidad, p.id AS producto_id, p.nombre, p.unidad, p.familia,
                   p.tipo_conservacion, p.precio, p.tiempo_descongelado_horas,
                   p.requiere_desinfeccion
            FROM recetas_ingredientes ri
            JOIN productos p ON p.id = ri.producto_id
            WHERE ri.receta_id = ?
            ORDER BY p.nombre
        """, (linea["receta_id"],)).fetchall()

        if not ingredientes:
            recetas_sin_ingredientes[linea["receta_id"]] = {
                "id": linea["receta_id"],
                "nombre": linea["receta_nombre"],
                "fecha": linea["fecha"],
                "origen": linea["origen"],
            }
            continue

        for ing in ingredientes:
            cantidad_base = _to_float(ing["cantidad"], 0.0)
            if cantidad_base <= 0:
                ingredientes_sin_cantidad.append({
                    "receta": linea["receta_nombre"],
                    "producto": ing["nombre"],
                    "fecha": linea["fecha"],
                })
                continue

            cantidad_necesaria = cantidad_base * multiplicador
            pid = ing["producto_id"]
            item = productos.setdefault(pid, {
                "producto_id": pid,
                "nombre": ing["nombre"],
                "unidad": ing["unidad"] or "",
                "familia": ing["familia"] or "",
                "tipo_conservacion": ing["tipo_conservacion"] or "",
                "cantidad_necesaria": 0.0,
                "coste_estimado": 0.0,
                "stock": None,
                "pendiente_compra": None,
                "departamento_id": linea["departamento_id"] if "departamento_id" in linea.keys() else None,
                "detalle": [],
                "requiere_desinfeccion": _producto_requiere_desinfeccion(ing),
                "horas_descongelado": _horas_descongelado(ing),
            })
            if not item.get("departamento_id") and "departamento_id" in linea.keys():
                item["departamento_id"] = linea["departamento_id"]
            item["cantidad_necesaria"] += cantidad_necesaria
            item["coste_estimado"] += cantidad_necesaria * _to_float(ing["precio"], 0.0)
            item["detalle"].append({
                "fecha": linea["fecha"],
                "hora_produccion": linea.get('hora_produccion'),
                "turno": linea["turno"],
                "receta": linea["receta_nombre"],
                "cantidad": cantidad_necesaria,
                "raciones": _to_float(linea["raciones_planificadas"], multiplicador * raciones_base) if "raciones_planificadas" in linea.keys() else multiplicador * raciones_base,
                "estacion": linea["estacion_buffet"] if "estacion_buffet" in linea.keys() else "",
                "regla": linea["regla_calculo"] if "regla_calculo" in linea.keys() else "",
                "origen": linea["origen"],
                "parte_id": linea["parte_id"],
            })

            if item["horas_descongelado"] > 0:
                fecha_alerta = _inicio_preparacion_produccion(linea['fecha'], item['horas_descongelado'], linea.get('hora_produccion'))
                alertas_descongelado.append({
                    "producto_id": pid,
                    "producto": ing["nombre"],
                    "fecha_produccion": linea["fecha"],
                    "fecha_alerta": fecha_alerta,
                    "hora_estimada": not bool(linea.get('hora_produccion')),
                    "horas": item["horas_descongelado"],
                    "cantidad": cantidad_necesaria,
                    "unidad": ing["unidad"] or "",
                    "parte_id": linea["parte_id"],
                })

            if item["requiere_desinfeccion"]:
                clave = (linea["fecha"], pid)
                actual = desinfecciones.setdefault(clave, {
                    "fecha": linea["fecha"],
                    "producto_id": pid,
                    "producto": ing["nombre"],
                    "cantidad": 0.0,
                    "unidad": ing["unidad"] or "",
                })
                actual["cantidad"] += cantidad_necesaria

    for item in productos.values():
        stock = _stock_producto(conn, item["producto_id"], empresa_id)
        por_fecha = {}
        for detalle in item['detalle']:
            limite = _inicio_preparacion_produccion(detalle['fecha'], item['horas_descongelado'], detalle.get('hora_produccion')).date().isoformat()
            por_fecha[limite] = por_fecha.get(limite, 0.0) + detalle['cantidad']
        acumulado = 0.0
        faltante = 0.0
        pendiente = 0.0
        for fecha in sorted(por_fecha):
            acumulado += por_fecha[fecha]
            pendiente = _pendiente_compra_producto(conn, item['producto_id'], empresa_id, fecha)
            faltante = max(faltante, acumulado - stock - pendiente)
        item["stock"] = stock
        item["pendiente_compra"] = pendiente
        item["faltante"] = max(faltante, 0.0)

    productos_ordenados = sorted(
        productos.values(),
        key=lambda x: (x["faltante"] <= 0, x["familia"], x["nombre"])
    )
    return {
        "lineas": lineas,
        "productos": productos_ordenados,
        "recetas_sin_ingredientes": list(recetas_sin_ingredientes.values()),
        "ingredientes_sin_cantidad": ingredientes_sin_cantidad,
        "coherencia": coherencia,
        "alertas_descongelado": alertas_descongelado,
        "desinfecciones": sorted(desinfecciones.values(), key=lambda x: (x["fecha"], x["producto"])),
        "totales": {
            "recetas_planificadas": len(lineas),
            "productos": len(productos_ordenados),
            "faltantes": sum(1 for p in productos_ordenados if p["faltante"] > 0),
            "coste_estimado": sum(p["coste_estimado"] for p in productos_ordenados),
            "origen_partes": sum(1 for linea in lineas if linea.get("origen") == "parte"),
            "origen_planificacion": sum(1 for linea in lineas if linea.get("origen") == "planificacion"),
            "origen_menu": sum(1 for linea in lineas if linea.get("origen") == "menu"),
        }
    }

# ============================================
# LISTADO DE PARTES DE PRODUCCIÓN
# ============================================
@produccion_bp.route('/')
@login_required
def produccion_index():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro
    _asegurar_planificacion_schema(conn)
    conn.commit()

    fecha = request.args.get('fecha') or datetime.now().strftime('%Y-%m-%d')
    ver_todo = request.args.get('ver_todo') == '1'
    scope_sql, scope_params = _scope_partes_produccion(conn, "pp", empresa_id)

    partes = conn.execute(f"""
        SELECT pp.*, 
               COUNT(lp.id) as num_lineas,
               COALESCE(SUM(lp.raciones_planificadas), 0) AS raciones_planificadas,
               SUM(CASE WHEN lp.estado = 'pendiente' THEN 1 ELSE 0 END) AS pendientes,
               SUM(CASE WHEN lp.estado = 'en_proceso' THEN 1 ELSE 0 END) AS en_proceso,
               SUM(CASE WHEN lp.estado = 'completado' THEN 1 ELSE 0 END) AS completadas,
               pv.nombre as punto_venta_nombre,
               s.nombre AS subdepartamento_nombre
        FROM partes_produccion pp
        LEFT JOIN lineas_produccion lp ON pp.id = lp.parte_id
        LEFT JOIN puntos_venta pv ON pp.punto_venta_id = pv.id
        LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
        WHERE {scope_sql}
          AND (? = 1 OR date(pp.fecha) = date(?))
        GROUP BY pp.id
        ORDER BY pp.fecha DESC, pp.turno, s.nombre, pp.id DESC
        LIMIT 200
    """, scope_params + [1 if ver_todo else 0, fecha]).fetchall()

    costes_servicios = _resumen_costes_produccion(conn, empresa_id, fecha, fecha)
    costes_totales = _totales_resumen_costes(costes_servicios)
    conn.close()
    
    return render_template('produccion/index.html',
                           partes=partes,
                           fecha=fecha,
                           ver_todo=ver_todo,
                           costes_servicios=costes_servicios,
                           costes_totales=costes_totales)


@produccion_bp.route('/costes')
@login_required
def costes_produccion():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro
    _asegurar_planificacion_schema(conn)
    conn.commit()

    fecha_desde = request.args.get('fecha_desde') or datetime.now().strftime('%Y-%m-%d')
    fecha_hasta = request.args.get('fecha_hasta') or fecha_desde
    servicio = (request.args.get('servicio') or '').strip()
    scope_sql, scope_params = _scope_partes_produccion(conn, "partes_produccion", empresa_id)

    servicios = conn.execute(f"""
        SELECT DISTINCT COALESCE(NULLIF(turno, ''), 'Sin servicio') AS servicio
        FROM partes_produccion
        WHERE {scope_sql}
          AND turno IS NOT NULL
        ORDER BY servicio
    """, scope_params).fetchall()

    resumen_servicios = _resumen_costes_produccion(
        conn, empresa_id, fecha_desde, fecha_hasta, servicio
    )
    detalle_centros = _resumen_costes_produccion(
        conn, empresa_id, fecha_desde, fecha_hasta, servicio, incluir_centro=True
    )
    detalle_platos = _detalle_costes_platos_produccion(
        conn, empresa_id, fecha_desde, fecha_hasta, servicio
    )
    totales = _totales_resumen_costes(resumen_servicios)
    conn.close()

    return render_template(
        'produccion/costes.html',
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        servicio=servicio,
        servicios=servicios,
        resumen_servicios=resumen_servicios,
        detalle_centros=detalle_centros,
        detalle_platos=detalle_platos,
        totales=totales,
    )

# ============================================
# NUEVO PARTE DE PRODUCCIÓN
# ============================================
@produccion_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_parte():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    if request.method == 'POST':
        bloqueo = _requiere_gestion_produccion(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_produccion(conn)
        if bloqueo_centro:
            return bloqueo_centro
        fecha = request.form.get('fecha')
        turno = request.form.get('turno')
        responsable = request.form.get('responsable', session.get('user_nombre'))
        punto_venta_id = request.form.get('punto_venta_id')
        observaciones = request.form.get('observaciones', '')
        centro_id = centro_activo_id()
        
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO partes_produccion 
            (fecha, turno, responsable, punto_venta_id, observaciones, empresa_id, estado, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, 'planificado', ?)
        """, (fecha, turno, responsable, punto_venta_id, observaciones, empresa_id, centro_id))
        
        parte_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        flash('✅ Parte de producción creado. Ahora añade las líneas.', 'success')
        return redirect(url_for('produccion.editar_parte', parte_id=parte_id))
    
    centro_id = centro_activo_id()
    pv_ids = ids_puntos_venta_ambito(conn, empresa_id, centro_id)
    pv_sql = f" AND id IN ({','.join('?' for _ in pv_ids)})" if pv_ids else (" AND 1 = 0" if centro_id else "")
    puntos_venta = conn.execute(f"""
        SELECT id, nombre FROM puntos_venta 
        WHERE activo = 1 AND empresa_id = ?
          {"AND centro_id = ?" if centro_id else ""}
          {pv_sql}
        ORDER BY nombre
    """, [empresa_id] + ([centro_id] if centro_id else []) + pv_ids).fetchall()
    conn.close()
    
    return render_template('produccion/nuevo_parte.html', 
                         puntos_venta=puntos_venta,
                         now=datetime.now())

# ============================================
# VER DETALLE DE UN PARTE
# ============================================
@produccion_bp.route('/<int:parte_id>')
@login_required
def ver_parte(parte_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    scope_sql, scope_params = _scope_partes_produccion(conn, "pp", empresa_id)
    parte = conn.execute(f"""
        SELECT pp.*, pv.nombre as punto_venta_nombre
        FROM partes_produccion pp
        LEFT JOIN puntos_venta pv ON pp.punto_venta_id = pv.id
        WHERE pp.id = ? AND {scope_sql}
    """, [parte_id] + scope_params).fetchone()
    
    if not parte:
        flash('Parte no encontrado', 'danger')
        return redirect(url_for('produccion.produccion_index'))
    centro_id = parte["centro_id"] if "centro_id" in parte.keys() and parte["centro_id"] else centro_activo_id()
    
    lineas = conn.execute("""
        SELECT lp.*, r.nombre as receta_nombre, r.descripcion,
               (SELECT COUNT(*) FROM consumo_produccion WHERE linea_produccion_id = lp.id) as tiene_consumo
        FROM lineas_produccion lp
        JOIN recetas r ON lp.receta_id = r.id
        WHERE lp.parte_id = ?
        ORDER BY lp.id
    """, (parte_id,)).fetchall()
    
    conn.close()
    return render_template('produccion/ver_parte.html', parte=parte, lineas=lineas)

# ============================================
# EDITAR PARTE (AÑADIR/QUITAR LÍNEAS)
# ============================================
@produccion_bp.route('/<int:parte_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_parte(parte_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    scope_sql, scope_params = _scope_partes_produccion(conn, "partes_produccion", empresa_id)
    parte_actual = conn.execute(
        f"SELECT * FROM partes_produccion WHERE id = ? AND {scope_sql}",
        [parte_id] + scope_params,
    ).fetchone()
    if not parte_actual:
        conn.close()
        flash('Parte no encontrado', 'danger')
        return redirect(url_for('produccion.produccion_index'))
    centro_id = parte_actual["centro_id"] if "centro_id" in parte_actual.keys() and parte_actual["centro_id"] else centro_activo_id()
    
    if request.method == 'POST':
        bloqueo = _requiere_gestion_produccion(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_produccion(conn)
        if bloqueo_centro:
            return bloqueo_centro
        registros = conn.execute("""
            SELECT
                (SELECT COUNT(*)
                 FROM consumo_produccion cp
                 JOIN lineas_produccion lp ON lp.id = cp.linea_produccion_id
                 WHERE lp.parte_id = ?) +
                (SELECT COUNT(*)
                 FROM produccion_ingredientes_reales pir
                 JOIN lineas_produccion lp ON lp.id = pir.linea_produccion_id
                 WHERE lp.parte_id = ?) +
                (SELECT COUNT(*) FROM consumo_extra WHERE parte_id = ?)
                AS total
        """, (parte_id, parte_id, parte_id)).fetchone()
        if _to_int(registros["total"] if registros else 0, 0) > 0 or (parte_actual["estado"] or "planificado") != "planificado":
            conn.close()
            flash('No se puede rehacer un parte con consumo, ingredientes reales o estado operativo. Crea un ajuste/incidencia.', 'danger')
            return redirect(url_for('produccion.gestionar_parte_subdepartamento', parte_id=parte_id))

        fecha = request.form.get('fecha')
        turno = request.form.get('turno')
        responsable = request.form.get('responsable')
        punto_venta_id = request.form.get('punto_venta_id') or None
        observaciones = request.form.get('observaciones', '')
        if punto_venta_id:
            pv_ids = ids_puntos_venta_ambito(conn, empresa_id, centro_id)
            pv = conn.execute("""
                SELECT id
                FROM puntos_venta
                WHERE id = ?
                  AND empresa_id = ?
                  AND COALESCE(activo, 1) = 1
                  AND (? IS NULL OR centro_id = ?)
            """, (punto_venta_id, empresa_id, centro_id, centro_id)).fetchone()
            if not pv or (pv_ids and int(punto_venta_id) not in [int(pid) for pid in pv_ids]):
                conn.close()
                flash('Punto de venta no válido para este centro', 'danger')
                return redirect(url_for('produccion.editar_parte', parte_id=parte_id))
        
        conn.execute("""
            UPDATE partes_produccion 
            SET fecha = ?, turno = ?, responsable = ?, punto_venta_id = ?, observaciones = ?
            WHERE id = ? AND empresa_id = ?
        """, (fecha, turno, responsable, punto_venta_id, observaciones, parte_id, empresa_id))
        
        receta_ids = request.form.getlist('receta_id[]')
        cantidades = request.form.getlist('cantidad[]')
        
        conn.execute("DELETE FROM lineas_produccion WHERE parte_id = ?", (parte_id,))
        
        for i in range(len(receta_ids)):
            if receta_ids[i] and cantidades[i]:
                receta = conn.execute("""
                    SELECT id
                    FROM recetas
                    WHERE id = ?
                      AND empresa_id = ?
                      AND COALESCE(activo, 1) = 1
                      AND (? IS NULL OR COALESCE(centro_id, ?) = ?)
                """, (receta_ids[i], empresa_id, centro_id, centro_id, centro_id)).fetchone()
                if not receta:
                    conn.rollback()
                    conn.close()
                    flash('Una receta seleccionada no pertenece a este centro o no está activa', 'danger')
                    return redirect(url_for('produccion.editar_parte', parte_id=parte_id))
                cantidad_planificada = _to_float(cantidades[i], 0)
                if cantidad_planificada <= 0:
                    continue
                conn.execute("""
                    INSERT INTO lineas_produccion (parte_id, receta_id, cantidad_planificada)
                    VALUES (?, ?, ?)
                """, (parte_id, receta_ids[i], cantidad_planificada))
        
        conn.commit()
        conn.close()
        
        flash('✅ Parte actualizado', 'success')
        return redirect(url_for('produccion.gestionar_parte_subdepartamento', parte_id=parte_id))
    
    parte = parte_actual
    
    if not parte:
        flash('Parte no encontrado', 'danger')
        return redirect(url_for('produccion.produccion_index'))
    
    centro_id = parte["centro_id"] if "centro_id" in parte.keys() and parte["centro_id"] else centro_activo_id()

    lineas = conn.execute("""
        SELECT lp.*, r.nombre as receta_nombre
        FROM lineas_produccion lp
        JOIN recetas r ON lp.receta_id = r.id
        WHERE lp.parte_id = ?
    """, (parte_id,)).fetchall()
    
    recetas = conn.execute("""
        SELECT id, nombre
        FROM recetas
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND (? IS NULL OR COALESCE(centro_id, ?) = ?)
        ORDER BY nombre
    """, (empresa_id, centro_id, centro_id, centro_id)).fetchall()
    puntos_venta = conn.execute("""
        SELECT id, nombre
        FROM puntos_venta
        WHERE activo = 1 AND empresa_id = ?
          AND (? IS NULL OR centro_id = ?)
        ORDER BY nombre
    """, (empresa_id, centro_id, centro_id)).fetchall()
    conn.close()
    
    return render_template('produccion/editar_parte.html',
                         parte=parte,
                         lineas=lineas,
                         recetas=recetas,
                         puntos_venta=puntos_venta)

# ============================================
# ELIMINAR PARTE
# ============================================
@produccion_bp.route('/<int:parte_id>/eliminar', methods=['POST'])
@login_required
def eliminar_parte(parte_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro

    scope_sql, scope_params = _scope_partes_produccion(conn, "partes_produccion", empresa_id)
    parte = conn.execute(
        f"SELECT estado FROM partes_produccion WHERE id = ? AND {scope_sql}",
        [parte_id] + scope_params,
    ).fetchone()
    
    if parte and parte['estado'] == 'planificado':
        conn.execute("DELETE FROM lineas_produccion WHERE parte_id = ?", (parte_id,))
        conn.execute("DELETE FROM partes_produccion WHERE id = ? AND empresa_id = ?", (parte_id, empresa_id))
        conn.commit()
        flash('✅ Parte eliminado', 'success')
    else:
        flash('❌ No se puede eliminar un parte en curso o completado', 'danger')
    
    conn.close()
    return redirect(url_for('produccion.produccion_index'))

# ============================================
# INICIAR PARTE
# ============================================
@produccion_bp.route('/<int:parte_id>/iniciar', methods=['POST'])
@login_required
def iniciar_parte(parte_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro

    scope_sql, scope_params = _scope_partes_produccion(conn, "partes_produccion", empresa_id)
    parte = conn.execute(
        f"SELECT * FROM partes_produccion WHERE id = ? AND {scope_sql}",
        [parte_id] + scope_params,
    ).fetchone()
    
    if not parte:
        conn.close()
        flash('❌ Parte no encontrado', 'danger')
        return redirect(url_for('produccion.produccion_index'))
    
    if parte['estado'] != 'planificado':
        conn.close()
        flash(f'⚠️ No se puede iniciar un parte en estado "{parte["estado"]}"', 'warning')
        return redirect(url_for('produccion.ver_parte', parte_id=parte_id))
    
    conn.execute("""
        UPDATE partes_produccion 
        SET estado = 'en_curso', hora_inicio = datetime('now')
        WHERE id = ? AND empresa_id = ?
    """, (parte_id, empresa_id))
    
    conn.commit()
    conn.close()
    
    flash('✅ Parte iniciado correctamente', 'success')
    return redirect(url_for('produccion.ver_parte', parte_id=parte_id))

# ============================================
# COMPLETAR PARTE (DESCONTAR STOCK)
# ============================================
@produccion_bp.route('/<int:parte_id>/completar', methods=['POST'])
@login_required
def completar_parte(parte_id, linea_id=None):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro
    _asegurar_planificacion_schema(conn)
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')

    scope_sql, scope_params = _scope_partes_produccion(conn, "partes_produccion", empresa_id)
    parte = conn.execute(
        f"SELECT * FROM partes_produccion WHERE id = ? AND {scope_sql}",
        [parte_id] + scope_params,
    ).fetchone()
    if not parte:
        conn.close()
        flash('Parte no encontrado', 'danger')
        return redirect(url_for('produccion.produccion_index'))
    estado_parte = (parte["estado"] or "").strip().lower()
    if estado_parte == "completado":
        conn.close()
        flash('Este parte ya estaba completado. No se ha descontado stock de nuevo.', 'warning')
        return redirect(url_for('produccion.ver_parte', parte_id=parte_id))
    if estado_parte not in {"en_curso", "planificado"}:
        conn.close()
        flash(f'No se puede completar un parte en estado "{parte["estado"]}".', 'warning')
        return redirect(url_for('produccion.ver_parte', parte_id=parte_id))

    if linea_id is not None and conn.execute('SELECT 1 FROM consumo_produccion WHERE linea_produccion_id=? LIMIT 1', (linea_id,)).fetchone():
        conn.close()
        flash('Este parte ya tiene consumos de stock registrados. No se ha descontado stock de nuevo.', 'warning')
        return redirect(url_for('produccion.ver_parte', parte_id=parte_id))
    
    centro_id = parte["centro_id"] if "centro_id" in parte.keys() and parte["centro_id"] else centro_activo_id()

    lineas = conn.execute("""
        SELECT lp.*, r.nombre as receta_nombre
        FROM lineas_produccion lp
        JOIN recetas r ON lp.receta_id = r.id
        WHERE lp.parte_id = ?
    """, (parte_id,)).fetchall()
    if linea_id is not None and not any(l['id'] == linea_id for l in lineas):
        conn.close()
        abort(404)
    
    errores = []
    
    for linea in lineas:
        if linea_id is not None and linea['id'] != linea_id:
            continue
        if conn.execute('SELECT 1 FROM consumo_produccion WHERE linea_produccion_id=? LIMIT 1', (linea['id'],)).fetchone():
            continue
        cantidad_real = linea['cantidad_real'] if linea['cantidad_real'] is not None else linea['cantidad_planificada']
        if cantidad_real is None or not math.isfinite(_to_float(cantidad_real, float('nan'))) or cantidad_real < 0:
            errores.append(f"Cantidad de produccion no valida para {linea['receta_nombre']}")
            continue
        
        omitidos = conn.execute("""
            SELECT receta_ingrediente_id FROM ingredientes_omitidos
            WHERE parte_id = ?
        """, (parte_id,)).fetchall()
        
        omitidos_ids = [o['receta_ingrediente_id'] for o in omitidos]

        params_actuales = [linea["id"]]
        filtro_omitidos_actuales = ""
        if omitidos_ids:
            filtro_omitidos_actuales = f" AND pir.receta_ingrediente_id NOT IN ({','.join('?' * len(omitidos_ids))})"
            params_actuales.extend(omitidos_ids)
        ingredientes_reales = conn.execute(f"""
            SELECT
                pir.receta_ingrediente_id AS id,
                pir.producto_id,
                pir.cantidad_real AS cantidad_necesaria,
                p.nombre,
                p.unidad,
                p.familia,
                p.tipo_conservacion,
                p.tiempo_descongelado_horas,
                p.precio
            FROM produccion_ingredientes_reales pir
            JOIN productos p ON p.id = pir.producto_id
            WHERE pir.linea_produccion_id = ?
              AND COALESCE(pir.cantidad_real, 0) > 0
              {filtro_omitidos_actuales}
        """, params_actuales).fetchall()

        cantidades_son_totales = bool(ingredientes_reales)

        if cantidades_son_totales:
            ingredientes = ingredientes_reales
        elif omitidos_ids:
            placeholders = ','.join('?' * len(omitidos_ids))
            ingredientes = conn.execute(f"""
                SELECT ri.*, p.nombre, p.unidad, p.familia, p.tipo_conservacion, p.tiempo_descongelado_horas, p.precio
                FROM recetas_ingredientes ri
                JOIN productos p ON ri.producto_id = p.id
                WHERE ri.receta_id = ? AND ri.id NOT IN ({placeholders})
            """, [linea['receta_id']] + omitidos_ids).fetchall()
        else:
            ingredientes = conn.execute("""
                SELECT ri.*, p.nombre, p.unidad, p.familia, p.tipo_conservacion, p.tiempo_descongelado_horas, p.precio
                FROM recetas_ingredientes ri
                JOIN productos p ON ri.producto_id = p.id
                WHERE ri.receta_id = ?
            """, (linea['receta_id'],)).fetchall()
        
        if not ingredientes and cantidad_real > 0:
            errores.append(f"La receta {linea['receta_nombre']} no tiene ingredientes utilizables. Revisa la ficha antes de producir.")
            continue
        for ing in ingredientes:
            cantidad_necesaria = (
                ing['cantidad_necesaria']
                if cantidades_son_totales
                else ing['cantidad'] * cantidad_real
            )
            if cantidad_necesaria == 0:
                continue
            if cantidad_necesaria < 0:
                errores.append(f"Cantidad negativa para {ing['nombre']}")
                continue
            
            lotes, requiere_descongelado = _lotes_disponibles_para_consumo(conn, ing, empresa_id, centro_id)
            
            if not lotes:
                if requiere_descongelado:
                    errores.append(f"{ing['nombre']} requiere un lote descongelado y APTO antes de producir")
                else:
                    errores.append(f"Falta stock de {ing['nombre']}")
                continue
            
            cantidad_a_consumir = cantidad_necesaria
            
            for lote in lotes:
                if cantidad_a_consumir <= 0:
                    break
                consumir = min(lote['cantidad_actual'], cantidad_a_consumir)
                
                coste_unitario = _to_float(
                    lote["coste_unitario"] if "coste_unitario" in lote.keys() and lote["coste_unitario"] is not None else None,
                    None,
                )
                if coste_unitario is None or coste_unitario <= 0:
                    coste_unitario = _to_float(ing["precio"] if "precio" in ing.keys() else 0, 0.0)
                conn.execute("""
                    INSERT INTO consumo_produccion (
                        linea_produccion_id, lote_id, producto_id,
                        cantidad_consumida, coste_unitario, coste_total, empresa_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    linea['id'],
                    lote['id'],
                    ing['producto_id'],
                    consumir,
                    coste_unitario,
                    consumir * coste_unitario,
                    empresa_id,
                ))

                conn.execute("""
                    INSERT INTO movimientos_lotes (
                        lote_id, tipo, cantidad, destino_tipo, destino_id,
                        usuario, observaciones, documento, empresa_id
                    )
                    VALUES (?, 'consumo_produccion', ?, 'parte_produccion', ?, ?, ?, ?, ?)
                """, (
                    lote['id'],
                    consumir,
                    parte_id,
                    session.get('user_nombre'),
                    f"Consumo en {linea['receta_nombre']}",
                    f"Parte {parte_id}",
                    empresa_id
                ))
                
                _descontar_lote_consumido(conn, lote, consumir, empresa_id)
                
                cantidad_a_consumir -= consumir
            
            if cantidad_a_consumir > 0:
                errores.append(f"Stock insuficiente de {ing['nombre']} (faltan {cantidad_a_consumir})")
    
    extras = conn.execute("""
        SELECT * FROM consumo_extra
        WHERE parte_id = ?
    """, (parte_id,)).fetchall()
    if linea_id is not None:
        extras = []
    
    producto_centro_sql, producto_centro_params = _filtro_producto_visible_centro(conn, "p", centro_id)
    for extra in extras:
        producto_extra = conn.execute(f"""
            SELECT id AS producto_id, nombre, unidad, familia, tipo_conservacion, tiempo_descongelado_horas, precio
            FROM productos p
            WHERE id = ? AND empresa_id = ?
              {producto_centro_sql}
        """, [extra['producto_id'], empresa_id] + producto_centro_params).fetchone()
        if not producto_extra:
            errores.append(f"Producto extra no encontrado (ID {extra['producto_id']})")
            continue
        lotes, requiere_descongelado = _lotes_disponibles_para_consumo(conn, producto_extra, empresa_id, centro_id)
        
        if not lotes:
            if requiere_descongelado:
                errores.append(f"{producto_extra['nombre']} requiere un lote descongelado y APTO antes de consumir")
            else:
                errores.append(f"Falta stock de {producto_extra['nombre']}")
            continue
        
        cantidad_a_consumir = extra['cantidad']
        
        for lote in lotes:
            if cantidad_a_consumir <= 0:
                break
            consumir = min(lote['cantidad_actual'], cantidad_a_consumir)
            _descontar_lote_consumido(conn, lote, consumir, empresa_id)
            conn.execute("""
                INSERT INTO movimientos_lotes (
                    lote_id, tipo, cantidad, destino_tipo, destino_id,
                    usuario, observaciones, documento, empresa_id
                )
                VALUES (?, 'consumo_extra', ?, 'parte_produccion', ?, ?, ?, ?, ?)
            """, (
                lote['id'],
                consumir,
                parte_id,
                session.get('user_nombre'),
                extra['motivo'] or 'Consumo extra',
                f"Parte {parte_id}",
                empresa_id
            ))
            cantidad_a_consumir -= consumir
        
        if cantidad_a_consumir > 0:
            errores.append(f"Stock insuficiente para producto extra (faltan {cantidad_a_consumir})")
    
    if errores:
        conn.rollback()
        conn.close()
        for error in errores[:5]:
            flash(f'❌ {error}', 'danger')
        return redirect(url_for('produccion.ver_parte', parte_id=parte_id))
    
    conn.execute("""
        UPDATE lineas_produccion
        SET estado = 'completado',
            hora_fin = COALESCE(hora_fin, datetime('now')),
            cantidad_real = COALESCE(cantidad_real, cantidad_planificada)
        WHERE parte_id = ? AND (? IS NULL OR id = ?)
    """, (parte_id, linea_id, linea_id))

    conn.execute("""
        UPDATE lineas_produccion
        SET coste_real = COALESCE((
                SELECT SUM(COALESCE(cp.coste_total, COALESCE(cp.cantidad_consumida, 0) * COALESCE(cp.coste_unitario, l.coste_unitario, p.precio, 0)))
                FROM consumo_produccion cp
                LEFT JOIN lotes l ON l.id = cp.lote_id
                LEFT JOIN productos p ON p.id = COALESCE(cp.producto_id, l.producto_id)
                WHERE cp.linea_produccion_id = lineas_produccion.id
            ), 0),
            coste_estimado = COALESCE((
                SELECT SUM(COALESCE(ri.cantidad, 0) * COALESCE(lineas_produccion.cantidad_real, lineas_produccion.cantidad_planificada, 1) * COALESCE(p.precio, 0))
                FROM recetas_ingredientes ri
                JOIN productos p ON p.id = ri.producto_id
                LEFT JOIN ingredientes_omitidos io
                  ON io.parte_id = lineas_produccion.parte_id
                 AND io.receta_ingrediente_id = ri.id
                WHERE ri.receta_id = lineas_produccion.receta_id
                  AND io.id IS NULL
            ), 0),
            coste_unitario_teorico = CASE
                WHEN COALESCE(raciones_planificadas, cantidad_real, cantidad_planificada, 0) > 0
                THEN COALESCE((
                    SELECT SUM(COALESCE(ri.cantidad, 0) * COALESCE(lineas_produccion.cantidad_real, lineas_produccion.cantidad_planificada, 1) * COALESCE(p.precio, 0))
                    FROM recetas_ingredientes ri
                    JOIN productos p ON p.id = ri.producto_id
                    LEFT JOIN ingredientes_omitidos io
                      ON io.parte_id = lineas_produccion.parte_id
                     AND io.receta_ingrediente_id = ri.id
                    WHERE ri.receta_id = lineas_produccion.receta_id
                      AND io.id IS NULL
                ), 0) / COALESCE(raciones_planificadas, cantidad_real, cantidad_planificada, 1)
                ELSE 0
            END
        WHERE parte_id = ?
    """, (parte_id,))

    conn.execute("""
        UPDATE partes_produccion 
        SET estado = ?, hora_fin = ?
        WHERE id = ?
    """, ('completado' if linea_id is None else 'en_curso', datetime.now() if linea_id is None else None, parte_id))

    for linea in lineas:
        if linea_id is not None and linea['id'] != linea_id:
            continue
        registrar_appcc_control(
            conn,
            empresa_id,
            "ELAB-01",
            valor=f"Parte {parte_id} | {linea['receta_nombre']} | raciones {linea['cantidad_real'] or linea['cantidad_planificada'] or 0}",
            resultado="ok",
            usuario=session.get('user_nombre') or 'admin',
            observaciones="Parte completado y stock descontado",
            origen_tabla="lineas_produccion",
            origen_id=linea["id"],
            codigo_operacion=f"completar_linea_{linea['id']}",
        )
    
    conn.commit()
    conn.close()
    
    flash('✅ Producción completada y stock actualizado (incluyendo extras)', 'success')
    return redirect(url_for('produccion.ver_parte', parte_id=parte_id))

# ============================================
# INCIDENCIAS EN PRODUCCIÓN
# ============================================
@produccion_bp.route('/<int:parte_id>/incidencias', methods=['GET', 'POST'])
@login_required
def incidencia_produccion(parte_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    scope_sql, scope_params = _scope_partes_produccion(conn, "partes_produccion", empresa_id)
    parte = conn.execute(
        f"SELECT * FROM partes_produccion WHERE id = ? AND {scope_sql}",
        [parte_id] + scope_params,
    ).fetchone()
    
    if not parte:
        conn.close()
        flash('Parte no encontrado', 'danger')
        return redirect(url_for('produccion.produccion_index'))
    centro_id = parte["centro_id"] if "centro_id" in parte.keys() and parte["centro_id"] else centro_activo_id()
    
    if request.method == 'POST':
        bloqueo = _requiere_gestion_produccion(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_produccion(conn)
        if bloqueo_centro:
            return bloqueo_centro
        lineas_ids = request.form.getlist('linea_id[]')
        
        for linea_id in lineas_ids:
            cantidad_real = request.form.get(f'cantidad_real_{linea_id}')
            if cantidad_real:
                conn.execute("""
                    UPDATE lineas_produccion 
                    SET cantidad_real = ? 
                    WHERE id = ?
                """, (float(cantidad_real), linea_id))
        
        productos_extra = request.form.getlist('producto_extra[]')
        cantidades_extra = request.form.getlist('cantidad_extra[]')
        motivos_extra = request.form.getlist('motivo_extra[]')
        producto_centro_sql, producto_centro_params = _filtro_producto_visible_centro(conn, "p", centro_id)
        
        for i in range(len(productos_extra)):
            if productos_extra[i] and cantidades_extra[i]:
                producto_extra_valido = conn.execute(f"""
                    SELECT id
                    FROM productos p
                    WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1
                      {producto_centro_sql}
                """, [productos_extra[i], empresa_id] + producto_centro_params).fetchone()
                if not producto_extra_valido:
                    conn.rollback()
                    conn.close()
                    flash('Un producto extra no pertenece al centro activo', 'danger')
                    return redirect(url_for('produccion.gestionar_parte_subdepartamento', parte_id=parte_id))
                conn.execute("""
                    INSERT INTO consumo_extra (parte_id, producto_id, cantidad, motivo, usuario, fecha)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                """, (parte_id, productos_extra[i], float(cantidades_extra[i]), 
                      motivos_extra[i], session.get('user_nombre')))
        
        omitidos = request.form.getlist('ingrediente_omitido[]')
        
        for ing_id in omitidos:
            conn.execute("""
                INSERT INTO ingredientes_omitidos (parte_id, receta_ingrediente_id, motivo, usuario, fecha)
                VALUES (?, ?, ?, ?, datetime('now'))
            """, (parte_id, ing_id, 'No disponible', session.get('user_nombre')))
        
        conn.commit()
        conn.close()
        
        flash('✅ Incidencias registradas', 'success')
        return redirect(url_for('produccion.ver_parte', parte_id=parte_id))
    
    lineas = conn.execute("""
        SELECT lp.*, r.nombre as receta_nombre
        FROM lineas_produccion lp
        JOIN recetas r ON lp.receta_id = r.id
        WHERE lp.parte_id = ?
    """, (parte_id,)).fetchall()
    
    lineas_con_ingredientes = []
    
    for linea in lineas:
        ingredientes = conn.execute("""
            SELECT ri.*, p.nombre, p.unidad
            FROM recetas_ingredientes ri
            JOIN productos p ON ri.producto_id = p.id
            WHERE ri.receta_id = ?
        """, (linea['receta_id'],)).fetchall()
        lineas_con_ingredientes.append({'linea': linea, 'ingredientes': ingredientes})
    
    producto_centro_sql, producto_centro_params = _filtro_producto_visible_centro(conn, "p", centro_id)
    productos = conn.execute(f"""
        SELECT id, nombre, unidad
        FROM productos p
        WHERE activo = 1 AND empresa_id = ?
          {producto_centro_sql}
        ORDER BY nombre
    """, [empresa_id] + producto_centro_params).fetchall()
    
    conn.close()
    
    return render_template('produccion/incidencia_produccion.html',
                         parte=parte,
                         lineas=lineas_con_ingredientes,
                         productos=productos)

# ============================================
# MERMAS
# ============================================
@produccion_bp.route('/mermas')
@login_required
def lista_mermas():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    producto_centro_sql, producto_centro_params = _filtro_producto_visible_centro(conn, "p")
    lotes_join, lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, "l")

    mermas = conn.execute(f"""
        SELECT m.*, p.nombre as producto_nombre, p.unidad, l.codigo_lote
        FROM mermas m
        JOIN productos p ON m.producto_id = p.id
        LEFT JOIN lotes l ON m.lote_id = l.id
        {lotes_join}
        WHERE m.empresa_id = ?
          {producto_centro_sql}
          {lotes_centro_sql}
        ORDER BY m.fecha DESC
        LIMIT 100
    """, [empresa_id] + producto_centro_params + lotes_centro_params).fetchall()
    conn.close()
    
    return render_template('produccion/mermas.html', mermas=mermas)

@produccion_bp.route('/mermas/nueva', methods=['GET', 'POST'])
@login_required
def nueva_merma():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro
    _asegurar_planificacion_schema(conn)
    conn.commit()
    
    if request.method == 'POST':
        bloqueo = _requiere_gestion_produccion(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_produccion(conn)
        if bloqueo_centro:
            return bloqueo_centro
        producto_id = request.form['producto_id']
        lote_id = request.form.get('lote_id')
        cantidad = float(request.form['cantidad'])
        motivo = request.form['motivo']
        destino = request.form.get('destino', 'basura')
        observaciones = request.form.get('observaciones', '')
        responsable = session.get('user_nombre')
        producto_centro_sql, producto_centro_params = _filtro_producto_visible_centro(conn, "p")
        producto_valido = conn.execute(f"""
            SELECT id
            FROM productos p
            WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1
              {producto_centro_sql}
        """, [producto_id, empresa_id] + producto_centro_params).fetchone()
        if not producto_valido:
            conn.close()
            flash('Producto no valido para este centro', 'danger')
            return redirect(url_for('produccion.nueva_merma'))
        if lote_id:
            lotes_join, lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, "l")
            lote_valido = conn.execute(f"""
                SELECT l.id
                FROM lotes l
                {lotes_join}
                WHERE l.id = ?
                  AND l.producto_id = ?
                  AND l.empresa_id = ?
                  AND l.estado = 'activo'
                  AND COALESCE(l.cantidad_actual, 0) >= ?
                  {lotes_centro_sql}
            """, [lote_id, producto_id, empresa_id, cantidad] + lotes_centro_params).fetchone()
            if not lote_valido:
                conn.close()
                flash('Lote no valido o sin stock suficiente para este centro', 'danger')
                return redirect(url_for('produccion.nueva_merma'))
        
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO mermas (producto_id, lote_id, cantidad, motivo, destino, responsable, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (producto_id, lote_id, cantidad, motivo, destino, responsable, observaciones, empresa_id))
        merma_id = cursor.lastrowid

        producto_merma = conn.execute(
            "SELECT nombre, unidad FROM productos WHERE id = ? AND empresa_id = ?",
            (producto_id, empresa_id),
        ).fetchone()
        registrar_appcc_control(
            conn,
            empresa_id,
            "MERMA-01",
            valor=f"{producto_merma['nombre'] if producto_merma else producto_id} | {cantidad:g} {producto_merma['unidad'] if producto_merma else ''} | {destino}",
            resultado="ok" if motivo else "no_ok",
            usuario=responsable or 'admin',
            observaciones=observaciones or motivo or "Merma sin motivo informado",
            accion_correctiva="Completar motivo y revisar stock" if not motivo else "",
            origen_tabla="mermas",
            origen_id=merma_id,
            codigo_operacion=f"merma_{merma_id}",
        )
        
        if lote_id:
            conn.execute("""
                UPDATE lotes SET cantidad_actual = cantidad_actual - ? WHERE id = ? AND empresa_id = ?
            """, (cantidad, lote_id, empresa_id))
            conn.execute("""
                INSERT INTO movimientos_lotes (
                    lote_id, tipo, cantidad, destino_tipo, usuario, observaciones, documento, empresa_id
                )
                VALUES (?, 'merma', ?, ?, ?, ?, 'Merma produccion', ?)
            """, (
                lote_id,
                cantidad,
                destino,
                responsable,
                observaciones or motivo,
                empresa_id
            ))
        
        conn.commit()
        conn.close()
        
        flash('✅ Merma registrada', 'success')
        return redirect(url_for('produccion.lista_mermas'))
    
    producto_centro_sql, producto_centro_params = _filtro_producto_visible_centro(conn, "p")
    productos = conn.execute(f"""
        SELECT id, nombre, unidad
        FROM productos p
        WHERE activo = 1 AND empresa_id = ?
          {producto_centro_sql}
        ORDER BY nombre
    """, [empresa_id] + producto_centro_params).fetchall()

    lotes_join, lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, "l")
    lotes = conn.execute(f"""
        SELECT l.*, p.nombre as producto_nombre
        FROM lotes l
        {lotes_join}
        JOIN productos p ON l.producto_id = p.id
        WHERE l.estado = 'activo' AND l.cantidad_actual > 0 AND p.empresa_id = ?
          {producto_centro_sql}
          {lotes_centro_sql}
        ORDER BY l.fecha_caducidad
    """, [empresa_id] + producto_centro_params + lotes_centro_params).fetchall()
    
    conn.close()
    return render_template('produccion/nueva_merma.html',
                         productos=productos,
                         lotes=lotes)

# ============================================
# PLANIFICAR PRODUCCIÓN DESDE MENÚS
# ============================================
@produccion_bp.route('/planificar', methods=['GET', 'POST'])
@login_required
def planificar_produccion():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_planificacion_schema(conn)
    conn.commit()
    
    if request.method == 'POST':
        bloqueo = _requiere_gestion_produccion(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_produccion(conn)
        if bloqueo_centro:
            return bloqueo_centro
        fecha = request.form['fecha']
        pax = request.form.get('pax_previstos', '').strip()
        conn.close()
        return planificar_desde_fecha()
    
    fecha_hoy = datetime.now().strftime('%Y-%m-%d')
    
    centro_md_sql, centro_md_params = _filtro_centro_tabla(conn, "menus_diarios", "md")
    menus_hoy = conn.execute(f"""
        SELECT md.*, t.nombre as tipo_nombre,
               COUNT(DISTINCT ms.id) as num_secciones,
               COUNT(DISTINCT mp.id) as num_platos
        FROM menus_diarios md
        JOIN tipos_menu t ON md.tipo_menu_id = t.id
        LEFT JOIN menu_subdepartamentos ms ON md.id = ms.menu_diario_id
        LEFT JOIN menu_platos mp ON ms.id = mp.menu_subdepartamento_id
        WHERE md.fecha = ? AND md.empresa_id = ?
          {centro_md_sql}
        GROUP BY md.id
    """, [fecha_hoy, empresa_id] + centro_md_params).fetchall()

    centro_tipo_sql, centro_tipo_params = _filtro_centro_tabla(conn, "tipos_menu", "")
    tipos = conn.execute(f"""
        SELECT id, nombre
        FROM tipos_menu
        WHERE activo = 1 AND empresa_id = ?
          {centro_tipo_sql}
        ORDER BY nombre
    """, [empresa_id] + centro_tipo_params).fetchall()

    centro_pv_sql, centro_pv_params = _filtro_centro_tabla(conn, "puntos_venta", "")
    puntos = conn.execute(f"""
        SELECT id, nombre
        FROM puntos_venta
        WHERE activo = 1 AND empresa_id = ?
          {centro_pv_sql}
        ORDER BY nombre
    """, [empresa_id] + centro_pv_params).fetchall()
    
    conn.close()
    
    return render_template('produccion/planificar.html', 
                         fecha_hoy=fecha_hoy,
                         menus_hoy=menus_hoy,
                         tipos=tipos,
                         puntos=puntos,
                         now=datetime.now())


@produccion_bp.route('/necesidades', methods=['GET', 'POST'])
@login_required
def necesidades_planificacion():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_planificacion_schema(conn)
    conn.commit()

    fecha_desde = request.values.get('fecha_desde') or datetime.now().strftime('%Y-%m-%d')
    fecha_hasta = request.values.get('fecha_hasta') or (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')
    datos = _calcular_necesidades_planificacion(conn, empresa_id, fecha_desde, fecha_hasta)

    if request.method == 'POST' and request.form.get('accion') == 'generar_alertas':
        bloqueo = _requiere_gestion_produccion(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_produccion(conn)
        if bloqueo_centro:
            return bloqueo_centro
        generadas = 0
        sin_lote = 0
        cursor = conn.cursor()
        lotes_join, lotes_centro_sql, lotes_centro_params = _filtro_lotes_centro(conn, "l")
        for alerta in datos["alertas_descongelado"]:
            lote = conn.execute(f"""
                SELECT l.id, l.codigo_lote
                FROM lotes l
                {lotes_join}
                WHERE l.producto_id = ?
                  AND l.empresa_id = ?
                  AND l.estado = 'activo'
                  AND COALESCE(l.cantidad_actual, 0) > 0
                  {lotes_centro_sql}
                ORDER BY fecha_caducidad IS NULL, fecha_caducidad, fecha_recepcion
                LIMIT 1
            """, [alerta["producto_id"], empresa_id] + lotes_centro_params).fetchone()
            if not lote:
                sin_lote += 1
                continue

            existe = conn.execute("""
                SELECT id
                FROM alertas_programadas
                WHERE tipo = 'descongelado'
                  AND lote_id = ?
                  AND fecha_produccion = ?
                  AND empresa_id = ?
                LIMIT 1
            """, (lote["id"], alerta["fecha_produccion"], empresa_id)).fetchone()
            if existe:
                continue

            cursor.execute("""
                INSERT INTO alertas_programadas (
                    tipo, lote_id, parte_id, fecha_alerta, fecha_produccion,
                    mensaje, empresa_id, cantidad_necesaria, unidad
                )
                VALUES ('descongelado', ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lote["id"],
                alerta["parte_id"],
                alerta["fecha_alerta"].strftime('%Y-%m-%d %H:%M:%S'),
                alerta["fecha_produccion"],
                f"Descongelar {alerta['producto']} lote {lote['codigo_lote']} para produccion del {alerta['fecha_produccion']}",
                empresa_id,
                _to_float(alerta.get("cantidad"), 0.0),
                alerta.get("unidad") or "",
            ))
            cursor.execute("""
                UPDATE lotes
                SET estado_descongelado = COALESCE(estado_descongelado, 'pendiente')
                WHERE id = ?
            """, (lote["id"],))
            cursor.execute("""
                INSERT INTO movimientos_lotes (
                    lote_id, tipo, cantidad, destino_tipo, usuario, observaciones, documento, empresa_id
                )
                VALUES (?, 'alerta_descongelado', 0, 'appcc', ?, ?, ?, ?)
            """, (
                lote["id"],
                session.get('user_nombre') or 'admin',
                f"Alerta APPCC generada para produccion del {alerta['fecha_produccion']}",
                f"Parte produccion {alerta['parte_id'] or '-'}",
                empresa_id,
            ))
            generadas += 1

        conn.commit()
        conn.close()
        if sin_lote:
            flash(f'{sin_lote} alerta(s) no se han creado porque no hay lote activo.', 'warning')
        flash(f'{generadas} alerta(s) de descongelacion generadas.', 'success')
        return redirect(url_for('produccion.necesidades_planificacion', fecha_desde=fecha_desde, fecha_hasta=fecha_hasta))

    conn.close()
    return render_template(
        'produccion/necesidades.html',
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        datos=datos,
        now=datetime.now(),
    )

@produccion_bp.route('/planificar/fecha', methods=['POST'])
@login_required
def planificar_desde_fecha():
    fecha = request.values.get('fecha')
    pax_override = _to_int(request.values.get('pax') or request.values.get('pax_previstos'), 0)
    
    if not fecha:
        flash('❌ No se especificó fecha', 'danger')
        return redirect(url_for('produccion.planificar_produccion'))
    
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro
    _asegurar_planificacion_schema(conn)
    conn.commit()
    
    centro_ps_sql, centro_ps_params = _filtro_centro_tabla(conn, "planificacion_servicios", "ps")
    centro_md_sql, centro_md_params = _filtro_centro_tabla(conn, "menus_diarios", "md")
    menus = conn.execute(f"""
        SELECT ps.fecha AS fecha_servicio, ps.subdepartamento_id AS filtro_subdepartamento_id,
               md.*, t.nombre as tipo_nombre
        FROM planificacion_servicios ps
        JOIN menus_diarios md ON md.id = ps.menu_diario_id
        JOIN tipos_menu t ON ps.tipo_menu_id = t.id
        WHERE ps.fecha = ? AND ps.empresa_id = ?
          {centro_ps_sql}
        ORDER BY t.nombre, ps.orden
    """, [fecha, empresa_id] + centro_ps_params).fetchall()

    if not menus:
        menus = conn.execute(f"""
            SELECT md.fecha AS fecha_servicio, NULL AS filtro_subdepartamento_id,
                   md.*, t.nombre as tipo_nombre
            FROM menus_diarios md
            JOIN tipos_menu t ON md.tipo_menu_id = t.id
            WHERE md.fecha = ? AND md.empresa_id = ?
              {centro_md_sql}
            ORDER BY t.nombre
        """, [fecha, empresa_id] + centro_md_params).fetchall()
    
    if not menus:
        conn.close()
        flash('⚠️ No hay menús para esta fecha', 'warning')
        return redirect(url_for('produccion.planificar_produccion'))
    
    partes_creados = 0
    
    for menu in menus:
        creados, _lineas = _crear_partes_buffet_desde_menu(
            conn,
            menu,
            empresa_id,
            fecha_servicio=menu["fecha_servicio"] or menu["fecha"],
            filtro_subdepartamento_id=menu["filtro_subdepartamento_id"],
            pax_override=pax_override,
            observaciones=f"Desde planificacion {fecha}",
        )
        partes_creados += creados
        continue
        fecha_servicio = menu["fecha_servicio"] or menu["fecha"]
        pax_previstos = pax_override or _pax_previstos_servicio(conn, empresa_id, fecha_servicio, menu["tipo_nombre"])
        secciones = conn.execute("""
            SELECT ms.*, s.id as subdepartamento_id, s.nombre AS subdepartamento_nombre
            FROM menu_subdepartamentos ms
            JOIN subdepartamentos s ON ms.subdepartamento_id = s.id
            WHERE ms.menu_diario_id = ?
              AND (? IS NULL OR ms.subdepartamento_id = ?)
        """, (menu['id'], menu["filtro_subdepartamento_id"], menu["filtro_subdepartamento_id"])).fetchall()
        
        for seccion in secciones:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO partes_produccion 
                (fecha, turno, responsable, menu_diario_id, subdepartamento_id, 
                 observaciones, estado, empresa_id, pax_previstos, regla_calculo)
                VALUES (?, ?, ?, ?, ?, ?, 'planificado', ?, ?, ?)
            """, (
                fecha_servicio,
                menu['tipo_nombre'],
                session.get('user_nombre'),
                menu['id'],
                seccion['subdepartamento_id'],
                f"Desde planificacion {fecha_servicio}",
                empresa_id,
                pax_previstos,
                f"Buffet calculado con {pax_previstos} pax previstos",
            ))
            parte_id = cursor.lastrowid
            partes_creados += 1
            
            platos = conn.execute("""
                SELECT mp.receta_id, r.id, r.nombre, COALESCE(r.raciones_base, 4) AS raciones_base
                FROM menu_platos mp
                JOIN recetas r ON r.id = mp.receta_id
                WHERE mp.menu_subdepartamento_id = ?
            """, (seccion['id'],)).fetchall()
            
            for plato in platos:
                _insertar_linea_produccion_buffet(
                    conn,
                    parte_id,
                    plato,
                    pax_previstos,
                    menu["tipo_nombre"],
                    seccion["subdepartamento_nombre"],
                    seccion["seccion"],
                )
    
    conn.commit()
    conn.close()
    
    flash(f'✅ {partes_creados} partes de producción creados para el {fecha}', 'success')
    return redirect(url_for('produccion.produccion_departamentos', fecha=fecha))

@produccion_bp.route('/planificar/semanal', methods=['POST'])
@login_required
def planificar_desde_semanal():
    fecha_inicio = request.values.get('fecha_inicio')
    pax_override = _to_int(request.values.get('pax'), 0)
    
    if not fecha_inicio:
        flash('❌ No se especificó fecha de inicio', 'danger')
        return redirect(url_for('menus.vista_semanal_menus'))
    
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro
    _asegurar_planificacion_schema(conn)
    conn.commit()
    
    fecha_inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
    fecha_fin_dt = fecha_inicio_dt + timedelta(days=6)
    fecha_fin = fecha_fin_dt.strftime('%Y-%m-%d')
    
    centro_ps_sql, centro_ps_params = _filtro_centro_tabla(conn, "planificacion_servicios", "ps")
    centro_md_sql, centro_md_params = _filtro_centro_tabla(conn, "menus_diarios", "md")
    menus = conn.execute(f"""
        SELECT ps.fecha AS fecha_servicio, ps.subdepartamento_id AS filtro_subdepartamento_id,
               md.*, t.nombre as tipo_nombre
        FROM planificacion_servicios ps
        JOIN menus_diarios md ON md.id = ps.menu_diario_id
        JOIN tipos_menu t ON ps.tipo_menu_id = t.id
        WHERE date(ps.fecha) BETWEEN date(?) AND date(?) AND ps.empresa_id = ?
          {centro_ps_sql}
        ORDER BY ps.fecha, t.nombre, ps.orden
    """, [fecha_inicio, fecha_fin, empresa_id] + centro_ps_params).fetchall()

    if not menus:
        menus = conn.execute(f"""
            SELECT md.fecha AS fecha_servicio, NULL AS filtro_subdepartamento_id,
                   md.*, t.nombre as tipo_nombre
            FROM menus_diarios md
            JOIN tipos_menu t ON md.tipo_menu_id = t.id
            WHERE md.fecha >= ? AND md.fecha <= ? AND md.empresa_id = ?
              {centro_md_sql}
            ORDER BY md.fecha, t.nombre
        """, [fecha_inicio, fecha_fin, empresa_id] + centro_md_params).fetchall()
    
    if not menus:
        conn.close()
        flash('⚠️ No hay menús en esta semana', 'warning')
        return redirect(url_for('menus.vista_semanal_menus', fecha=fecha_inicio))
    
    partes_creados = 0
    
    for menu in menus:
        creados, _lineas = _crear_partes_buffet_desde_menu(
            conn,
            menu,
            empresa_id,
            fecha_servicio=menu["fecha_servicio"] or menu["fecha"],
            filtro_subdepartamento_id=menu["filtro_subdepartamento_id"],
            pax_override=pax_override,
            observaciones=f"Desde menu semanal {fecha_inicio}",
        )
        partes_creados += creados
        continue
        secciones = conn.execute("""
            SELECT ms.*, s.id as subdepartamento_id
            FROM menu_subdepartamentos ms
            JOIN subdepartamentos s ON ms.subdepartamento_id = s.id
            WHERE ms.menu_diario_id = ?
        """, (menu['id'],)).fetchall()
        
        for seccion in secciones:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO partes_produccion 
                (fecha, turno, responsable, menu_diario_id, subdepartamento_id, 
                 observaciones, estado, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, 'planificado', ?)
            """, (menu['fecha'], menu['tipo_nombre'], session.get('user_nombre'), 
                  menu['id'], seccion['subdepartamento_id'], 
                  f"Desde menú semanal {fecha_inicio}", empresa_id))
            parte_id = cursor.lastrowid
            partes_creados += 1
            
            platos = conn.execute("""
                SELECT mp.receta_id
                FROM menu_platos mp
                WHERE mp.menu_subdepartamento_id = ?
            """, (seccion['id'],)).fetchall()
            
            for plato in platos:
                conn.execute("""
                    INSERT INTO lineas_produccion 
                    (parte_id, receta_id, cantidad_planificada, estado)
                    VALUES (?, ?, ?, 'pendiente')
                """, (parte_id, plato['receta_id'], 1))
    
    conn.commit()
    conn.close()
    
    flash(f'✅ {partes_creados} partes de producción creados para la semana', 'success')
    return redirect(url_for('produccion.produccion_departamentos', fecha=fecha_inicio))

@produccion_bp.route('/planificar/menu', methods=['POST'])
@login_required
def planificar_desde_menu():
    menu_id = request.values.get('menu_id')
    fecha_servicio = request.values.get('fecha')
    pax_override = _to_int(request.values.get('pax'), 0)
    
    if not menu_id:
        flash('❌ No se especificó menú', 'danger')
        return redirect(url_for('menus.vista_semanal_menus'))
    
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro
    _asegurar_planificacion_schema(conn)
    conn.commit()
    
    centro_md_sql, centro_md_params = _filtro_centro_tabla(conn, "menus_diarios", "md")
    menu = conn.execute(f"""
        SELECT md.*, t.nombre as tipo_nombre
        FROM menus_diarios md
        JOIN tipos_menu t ON md.tipo_menu_id = t.id
        WHERE md.id = ? AND md.empresa_id = ?
          {centro_md_sql}
    """, [menu_id, empresa_id] + centro_md_params).fetchone()
    
    if not menu:
        conn.close()
        flash('❌ Menú no encontrado', 'danger')
        return redirect(url_for('menus.vista_semanal_menus'))

    partes_creados, total_lineas = _crear_partes_buffet_desde_menu(
        conn,
        menu,
        empresa_id,
        fecha_servicio=fecha_servicio or menu["fecha"],
        pax_override=pax_override,
        observaciones=f"Desde menu {menu_id}",
    )
    conn.commit()
    conn.close()

    if not partes_creados:
        flash('El menu no tiene secciones para generar produccion', 'warning')
        return redirect(url_for('menus.ver_menu_diario', menu_id=menu_id))

    flash(f'Parte(s) de produccion creados con {total_lineas} lineas', 'success')
    return redirect(url_for('produccion.produccion_departamentos', fecha=fecha_servicio or menu["fecha"]))

    secciones = conn.execute("""
        SELECT ms.*, s.id as subdepartamento_id
        FROM menu_subdepartamentos ms
        JOIN subdepartamentos s ON ms.subdepartamento_id = s.id
        WHERE ms.menu_diario_id = ?
    """, (menu_id,)).fetchall()
    
    if not secciones:
        conn.close()
        flash('⚠️ El menú no tiene secciones', 'warning')
        return redirect(url_for('menus.ver_menu_diario', menu_id=menu_id))
    
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO partes_produccion 
        (fecha, turno, responsable, menu_diario_id, observaciones, estado, empresa_id)
        VALUES (?, ?, ?, ?, ?, 'planificado', ?)
    """, (menu['fecha'], menu['tipo_nombre'], session.get('user_nombre'), 
          menu_id, f"Desde menú {menu_id}", empresa_id))
    
    parte_id = cursor.lastrowid
    total_lineas = 0
    
    for seccion in secciones:
        platos = conn.execute("""
            SELECT mp.receta_id
            FROM menu_platos mp
            WHERE mp.menu_subdepartamento_id = ?
        """, (seccion['id'],)).fetchall()
        
        for plato in platos:
            conn.execute("""
                INSERT INTO lineas_produccion 
                (parte_id, receta_id, cantidad_planificada, estado)
                VALUES (?, ?, ?, 'pendiente')
            """, (parte_id, plato['receta_id'], 1))
            total_lineas += 1
    
    conn.commit()
    conn.close()
    
    flash(f'✅ Parte de producción #{parte_id} creado con {total_lineas} líneas', 'success')
    return redirect(url_for('produccion.ver_parte', parte_id=parte_id))

# ============================================
# PRODUCCIÓN POR DEPARTAMENTOS
# ============================================
@produccion_bp.route('/departamentos')
@login_required
def produccion_departamentos():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    fecha = request.args.get('fecha', datetime.now().strftime('%Y-%m-%d'))
    
    departamentos = conn.execute("""
        SELECT d.*, 
               COUNT(DISTINCT pp.id) as num_partes,
               COUNT(DISTINCT lp.id) as num_lineas
        FROM departamentos d
        LEFT JOIN subdepartamentos s ON d.id = s.departamento_id
        LEFT JOIN partes_produccion pp ON pp.subdepartamento_id = s.id
            AND date(pp.fecha) = date(?)
            AND pp.empresa_id = d.empresa_id
        LEFT JOIN lineas_produccion lp ON pp.id = lp.parte_id
        WHERE d.empresa_id = ? AND d.activo = 1
        GROUP BY d.id
        ORDER BY d.nombre
    """, (fecha, empresa_id)).fetchall()
    
    conn.close()
    return render_template('produccion/departamentos.html', 
                         departamentos=departamentos,
                         fecha=fecha)

@produccion_bp.route('/departamentos/<int:depto_id>')
@login_required
def produccion_departamento(depto_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    fecha = request.args.get('fecha', datetime.now().strftime('%Y-%m-%d'))
    
    departamento = conn.execute("SELECT * FROM departamentos WHERE id = ? AND empresa_id = ?", 
                               (depto_id, empresa_id)).fetchone()
    
    subdepartamentos = conn.execute("""
        SELECT s.*,
               COUNT(DISTINCT pp.id) as num_partes,
               COUNT(DISTINCT lp.id) as num_lineas,
               GROUP_CONCAT(DISTINCT pp.id) as partes_ids
        FROM subdepartamentos s
        LEFT JOIN partes_produccion pp ON pp.subdepartamento_id = s.id
            AND date(pp.fecha) = date(?)
            AND pp.empresa_id = s.empresa_id
        LEFT JOIN lineas_produccion lp ON pp.id = lp.parte_id
        WHERE s.departamento_id = ? AND s.activo = 1 AND s.empresa_id = ?
        GROUP BY s.id
        ORDER BY s.nombre
    """, (fecha, depto_id, empresa_id)).fetchall()
    
    conn.close()
    return render_template('produccion/departamento.html',
                         departamento=departamento,
                         subdepartamentos=subdepartamentos,
                         fecha=fecha)

@produccion_bp.route('/subdepartamentos/<int:subdepto_id>')
@login_required
def produccion_subdepartamento(subdepto_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    fecha = request.args.get('fecha', datetime.now().strftime('%Y-%m-%d'))
    
    subdepartamento = conn.execute("""
        SELECT s.*, d.nombre as departamento_nombre
        FROM subdepartamentos s
        JOIN departamentos d ON s.departamento_id = d.id
        WHERE s.id = ? AND s.empresa_id = ?
    """, (subdepto_id, empresa_id)).fetchone()
    
    partes = conn.execute("""
        SELECT pp.*, 
               md.tipo_menu_id,
               t.nombre as tipo_nombre,
               COUNT(lp.id) as num_lineas,
               COALESCE(SUM(lp.raciones_planificadas), 0) AS raciones_planificadas,
               SUM(CASE WHEN lp.estado = 'pendiente' THEN 1 ELSE 0 END) as pendientes,
               SUM(CASE WHEN lp.estado = 'completado' THEN 1 ELSE 0 END) as completadas
        FROM partes_produccion pp
        LEFT JOIN menus_diarios md ON pp.menu_diario_id = md.id
        LEFT JOIN tipos_menu t ON md.tipo_menu_id = t.id
        LEFT JOIN lineas_produccion lp ON pp.id = lp.parte_id
        WHERE pp.subdepartamento_id = ? AND date(pp.fecha) = date(?) AND pp.empresa_id = ?
        GROUP BY pp.id
        ORDER BY pp.turno, pp.hora_inicio
    """, (subdepto_id, fecha, empresa_id)).fetchall()

    lineas = conn.execute("""
        SELECT lp.*,
               pp.id AS parte_id,
               pp.turno,
               pp.estado AS parte_estado,
               pp.pax_previstos,
               pp.regla_calculo AS parte_regla_calculo,
               r.nombre AS receta_nombre,
               COALESCE(r.raciones_base, 4) AS raciones_base,
               COUNT(ri.id) AS num_ingredientes
        FROM partes_produccion pp
        JOIN lineas_produccion lp ON lp.parte_id = pp.id
        JOIN recetas r ON r.id = lp.receta_id
        LEFT JOIN recetas_ingredientes ri ON ri.receta_id = r.id
        WHERE pp.subdepartamento_id = ?
          AND date(pp.fecha) = date(?)
          AND pp.empresa_id = ?
        GROUP BY lp.id
        ORDER BY pp.turno, r.nombre
    """, (subdepto_id, fecha, empresa_id)).fetchall()

    conn.close()
    return render_template('produccion/subdepartamento.html',
                         subdepartamento=subdepartamento,
                         partes=partes,
                         lineas=lineas,
                         total_raciones=sum(_to_float(l["raciones_planificadas"], 0) for l in lineas),
                         fecha=fecha)


# ============================================
# TERMINALES OPERATIVOS DE COCINA
# ============================================
@produccion_bp.route('/terminales')
@login_required
def terminales_cocina():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    fecha = request.args.get('fecha', datetime.now().strftime('%Y-%m-%d'))
    _asegurar_planificacion_schema(conn)
    conn.commit()

    subdepartamentos = conn.execute("""
        SELECT s.*, d.nombre AS departamento_nombre,
               COUNT(DISTINCT pp.id) AS partes,
               COUNT(lp.id) AS lineas,
               SUM(CASE WHEN COALESCE(lp.estado, 'pendiente') = 'pendiente' THEN 1 ELSE 0 END) AS pendientes,
               SUM(CASE WHEN COALESCE(lp.estado, 'pendiente') = 'en_proceso' THEN 1 ELSE 0 END) AS en_proceso,
               SUM(CASE WHEN COALESCE(lp.estado, 'pendiente') = 'completado' THEN 1 ELSE 0 END) AS completadas
        FROM subdepartamentos s
        JOIN departamentos d ON d.id = s.departamento_id
        LEFT JOIN partes_produccion pp ON pp.subdepartamento_id = s.id
            AND date(pp.fecha) = date(?)
            AND pp.empresa_id = s.empresa_id
        LEFT JOIN lineas_produccion lp ON lp.parte_id = pp.id
        WHERE s.empresa_id = ?
          AND COALESCE(s.activo, 1) = 1
          AND (
                LOWER(COALESCE(d.nombre, '')) LIKE '%cocina%'
                OR EXISTS (
                    SELECT 1 FROM partes_produccion pp2
                    WHERE pp2.subdepartamento_id = s.id
                      AND date(pp2.fecha) = date(?)
                      AND pp2.empresa_id = s.empresa_id
                )
          )
        GROUP BY s.id
        ORDER BY d.nombre, s.nombre
    """, (fecha, empresa_id, fecha)).fetchall()
    subdepartamentos = [
        s for s in subdepartamentos
        if _usuario_puede_terminal_subdepartamento(s)
    ]
    conn.close()
    return render_template(
        'produccion/terminales.html',
        subdepartamentos=subdepartamentos,
        fecha=fecha,
    )


@produccion_bp.route('/terminal/subdepartamentos/<int:subdepto_id>')
@login_required
def terminal_subdepartamento(subdepto_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    fecha = request.args.get('fecha', datetime.now().strftime('%Y-%m-%d'))
    servicio = (request.args.get('servicio') or '').strip()
    estado_linea = (request.args.get('estado_linea') or '').strip()
    if estado_linea not in {'pendiente', 'en_proceso', 'completado'}:
        estado_linea = ''
    _asegurar_planificacion_schema(conn)
    conn.commit()

    subdepartamento = conn.execute("""
        SELECT s.*, d.nombre AS departamento_nombre
        FROM subdepartamentos s
        JOIN departamentos d ON d.id = s.departamento_id
        WHERE s.id = ? AND s.empresa_id = ?
    """, (subdepto_id, empresa_id)).fetchone()
    if not subdepartamento:
        conn.close()
        flash('Subdepartamento no encontrado', 'danger')
        return redirect(url_for('produccion.terminales_cocina', fecha=fecha))
    if not _usuario_puede_terminal_subdepartamento(subdepartamento):
        conn.close()
        flash('No tienes permiso para abrir este terminal.', 'warning')
        return redirect(url_for('produccion.terminales_cocina', fecha=fecha))

    filtro_servicio = ""
    params = [subdepto_id, fecha, empresa_id]
    if servicio:
        filtro_servicio = " AND COALESCE(NULLIF(pp.turno, ''), 'Sin servicio') = ?"
        params.append(servicio)

    partes = conn.execute(f"""
        SELECT pp.*, md.tipo_menu_id, t.nombre AS tipo_nombre,
               COUNT(lp.id) AS lineas,
               SUM(CASE WHEN COALESCE(lp.estado, 'pendiente') = 'pendiente' THEN 1 ELSE 0 END) AS pendientes,
               SUM(CASE WHEN COALESCE(lp.estado, 'pendiente') = 'en_proceso' THEN 1 ELSE 0 END) AS en_proceso,
               SUM(CASE WHEN COALESCE(lp.estado, 'pendiente') = 'completado' THEN 1 ELSE 0 END) AS completadas
        FROM partes_produccion pp
        LEFT JOIN menus_diarios md ON md.id = pp.menu_diario_id
        LEFT JOIN tipos_menu t ON t.id = md.tipo_menu_id
        LEFT JOIN lineas_produccion lp ON lp.parte_id = pp.id
        WHERE pp.subdepartamento_id = ?
          AND date(pp.fecha) = date(?)
          AND pp.empresa_id = ?
          {filtro_servicio}
        GROUP BY pp.id
        ORDER BY pp.turno, pp.id
    """, params).fetchall()

    parte_ids = [p["id"] for p in partes]
    lineas_por_parte = {}
    resumen_produccion = {
        "total": 0,
        "pendiente": 0,
        "en_proceso": 0,
        "completado": 0,
    }
    if parte_ids:
        placeholders = ",".join("?" for _ in parte_ids)
        resumen_row = conn.execute(f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN COALESCE(estado, 'pendiente') = 'pendiente' THEN 1 ELSE 0 END) AS pendiente,
                   SUM(CASE WHEN COALESCE(estado, 'pendiente') = 'en_proceso' THEN 1 ELSE 0 END) AS en_proceso,
                   SUM(CASE WHEN COALESCE(estado, 'pendiente') = 'completado' THEN 1 ELSE 0 END) AS completado
            FROM lineas_produccion
            WHERE parte_id IN ({placeholders})
        """, parte_ids).fetchone()
        resumen_produccion = {
            "total": _to_int(resumen_row["total"] if resumen_row else 0, 0),
            "pendiente": _to_int(resumen_row["pendiente"] if resumen_row else 0, 0),
            "en_proceso": _to_int(resumen_row["en_proceso"] if resumen_row else 0, 0),
            "completado": _to_int(resumen_row["completado"] if resumen_row else 0, 0),
        }
        filtro_estado_linea = ""
        params_lineas = list(parte_ids)
        if estado_linea:
            filtro_estado_linea = "AND COALESCE(lp.estado, 'pendiente') = ?"
            params_lineas.append(estado_linea)
        lineas = conn.execute(f"""
            SELECT lp.*, r.nombre AS receta_nombre, r.descripcion, r.alergenos,
                   COALESCE(r.raciones_base, 4) AS raciones_base,
                   pp.id AS parte_id, pp.fecha AS parte_fecha, pp.turno AS parte_turno,
                   COALESCE(s.nombre, '') AS subdepartamento_nombre,
                   ple.id AS lote_elaborado_id,
                   ple.codigo_lote AS lote_elaborado_codigo,
                   COUNT(ri.id) AS num_ingredientes
            FROM lineas_produccion lp
            JOIN recetas r ON r.id = lp.receta_id
            JOIN partes_produccion pp ON pp.id = lp.parte_id
            LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
            LEFT JOIN recetas_ingredientes ri ON ri.receta_id = r.id
            LEFT JOIN produccion_lotes_elaborados ple ON ple.linea_produccion_id = lp.id
            WHERE lp.parte_id IN ({placeholders})
              {filtro_estado_linea}
            GROUP BY lp.id
            ORDER BY COALESCE(lp.estacion_buffet, ''), r.nombre
        """, params_lineas).fetchall()
        lineas = _anotar_contaminacion_cruzada([dict(linea) for linea in lineas])
        for linea in lineas:
            lineas_por_parte.setdefault(linea["parte_id"], []).append(linea)

    servicios = conn.execute("""
        SELECT DISTINCT COALESCE(NULLIF(turno, ''), 'Sin servicio') AS servicio
        FROM partes_produccion
        WHERE subdepartamento_id = ?
          AND date(fecha) = date(?)
          AND empresa_id = ?
        ORDER BY servicio
    """, (subdepto_id, fecha, empresa_id)).fetchall()
    resumen_servicios = _resumen_servicios_terminal(conn, empresa_id, fecha, partes)
    conn.close()
    return render_template(
        'produccion/terminal_subdepartamento.html',
        subdepartamento=subdepartamento,
        partes=partes,
        lineas_por_parte=lineas_por_parte,
        servicios=servicios,
        resumen_servicios=resumen_servicios,
        resumen_produccion=resumen_produccion,
        fecha=fecha,
        servicio=servicio,
        estado_linea=estado_linea,
    )


@produccion_bp.route('/terminal/lineas/<int:linea_id>/estado', methods=['POST'])
@login_required
def terminal_actualizar_linea(linea_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro
    _asegurar_planificacion_schema(conn)
    conn.commit()
    estado = request.form.get('estado') or 'pendiente'
    if estado not in {'pendiente', 'en_proceso', 'completado'}:
        estado = 'pendiente'
    cantidad_real_txt = (request.form.get('cantidad_real') or '').strip()
    observaciones = (request.form.get('observaciones') or '').strip()

    scope_sql, scope_params = _scope_partes_produccion(conn, "pp", empresa_id)
    linea = conn.execute(f"""
        SELECT lp.*, pp.id AS parte_id, pp.subdepartamento_id, pp.fecha, pp.estado AS parte_estado
        FROM lineas_produccion lp
        JOIN partes_produccion pp ON pp.id = lp.parte_id
        WHERE lp.id = ? AND {scope_sql}
    """, [linea_id] + scope_params).fetchone()
    if not linea:
        conn.close()
        flash('Linea no encontrada', 'danger')
        return redirect(url_for('produccion.terminales_cocina'))
    subdepartamento = conn.execute(
        "SELECT * FROM subdepartamentos WHERE id = ? AND empresa_id = ?",
        (linea["subdepartamento_id"], empresa_id),
    ).fetchone()
    if not _usuario_puede_terminal_subdepartamento(subdepartamento):
        conn.close()
        flash('No tienes permiso para modificar este terminal.', 'warning')
        return redirect(url_for('produccion.terminales_cocina'))

    if conn.execute('SELECT 1 FROM consumo_produccion WHERE linea_produccion_id=? LIMIT 1', (linea_id,)).fetchone():
        conn.close()
        flash('Esta elaboracion ya tiene consumos registrados y no admite cambios de cantidad o estado.', 'warning')
        return redirect(url_for('produccion.ver_parte', parte_id=linea['parte_id']))
    finalizar = estado == 'completado'
    if finalizar:
        estado = 'en_proceso'

    sets = ["estado = ?", "operario = ?", "observaciones = CASE WHEN ? = '' THEN observaciones ELSE ? END"]
    params = [estado, session.get('user_nombre') or 'admin', observaciones, observaciones]
    if cantidad_real_txt:
        sets.append("cantidad_real = ?")
        params.append(_to_float(cantidad_real_txt, 0.0))
    if estado == 'en_proceso':
        sets.append("hora_inicio = COALESCE(hora_inicio, datetime('now'))")
    elif estado == 'completado':
        sets.append("hora_inicio = COALESCE(hora_inicio, datetime('now'))")
        sets.append("hora_fin = datetime('now')")
    elif estado == 'pendiente':
        sets.append("hora_fin = NULL")
    params.append(linea_id)
    conn.execute(f"UPDATE lineas_produccion SET {', '.join(sets)} WHERE id = ?", params)

    if estado in {'en_proceso', 'completado'} and (linea['parte_estado'] or 'planificado') == 'planificado':
        conn.execute("""
            UPDATE partes_produccion
            SET estado = 'en_curso', hora_inicio = COALESCE(hora_inicio, datetime('now'))
            WHERE id = ? AND empresa_id = ?
        """, (linea['parte_id'], empresa_id))

    conn.commit()
    conn.close()
    if finalizar:
        return completar_parte(linea['parte_id'], linea_id=linea_id)
    flash('Linea actualizada en terminal', 'success')
    return redirect(url_for(
        'produccion.terminal_subdepartamento',
        subdepto_id=linea['subdepartamento_id'],
        fecha=str(linea['fecha'])[:10],
        servicio=request.form.get('servicio') or '',
        estado_linea=request.form.get('estado_linea') or '',
    ))


# ============================================
# GESTIONAR PARTE (AJUSTES FINOS)
# ============================================
@produccion_bp.route('/partes/<int:parte_id>/gestion', methods=['GET', 'POST'])
@login_required
def gestionar_parte_subdepartamento(parte_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_planificacion_schema(conn)
    conn.commit()
    
    scope_sql, scope_params = _scope_partes_produccion(conn, "pp", empresa_id)
    parte = conn.execute(f"""
        SELECT pp.*, md.fecha AS menu_fecha, t.nombre as tipo_nombre,
               s.nombre as subdepartamento_nombre,
               d.nombre as departamento_nombre
        FROM partes_produccion pp
        JOIN menus_diarios md ON pp.menu_diario_id = md.id
        JOIN tipos_menu t ON md.tipo_menu_id = t.id
        LEFT JOIN subdepartamentos s ON pp.subdepartamento_id = s.id
        LEFT JOIN departamentos d ON s.departamento_id = d.id
        WHERE pp.id = ? AND {scope_sql}
    """, [parte_id] + scope_params).fetchone()
    
    if not parte:
        conn.close()
        flash('Parte no encontrado', 'danger')
        return redirect(url_for('produccion.produccion_index'))

    centro_id = parte["centro_id"] if "centro_id" in parte.keys() and parte["centro_id"] else centro_activo_id()
    
    if request.method == 'POST':
        bloqueo = _requiere_gestion_produccion(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_produccion(conn)
        if bloqueo_centro:
            return bloqueo_centro
        lineas_ids = request.form.getlist('linea_id[]')
        
        for linea_id in lineas_ids:
            cantidad_real = request.form.get(f'cantidad_real_{linea_id}')
            estado = request.form.get(f'estado_{linea_id}')
            observaciones = request.form.get(f'observaciones_{linea_id}')
            
            cantidad_real_txt = (cantidad_real or "").strip()
            conn.execute("""
                UPDATE lineas_produccion
                SET cantidad_real = CASE WHEN ? = '' THEN cantidad_real ELSE ? END,
                    estado = ?,
                    observaciones = ?
                WHERE id = ? AND parte_id = ?
            """, (
                cantidad_real_txt,
                _to_float(cantidad_real_txt, 0.0),
                estado,
                observaciones,
                linea_id,
                parte_id,
            ))

            linea_control = conn.execute("""
                SELECT *
                FROM lineas_produccion
                WHERE id = ? AND parte_id = ?
            """, (linea_id, parte_id)).fetchone()
            if linea_control:
                _guardar_control_appcc_linea(conn, parte, linea_control, empresa_id)

        conn.execute("DELETE FROM ingredientes_omitidos WHERE parte_id = ?", (parte_id,))
        omitidos = request.form.getlist('ingrediente_omitido[]')
        for ing_id in omitidos:
            conn.execute("""
                INSERT INTO ingredientes_omitidos (parte_id, receta_ingrediente_id, motivo, usuario, fecha)
                VALUES (?, ?, ?, ?, datetime('now'))
            """, (parte_id, ing_id, 'No utilizado en produccion', session.get('user_nombre')))

        ingrediente_keys = request.form.getlist('ingrediente_key[]')
        for key in ingrediente_keys:
            try:
                linea_id_txt, ing_id_txt = key.split(':', 1)
                linea_id = int(linea_id_txt)
                ing_id = int(ing_id_txt)
            except Exception:
                continue

            cantidad_txt = (request.form.get(f'ingrediente_real_{linea_id}_{ing_id}') or '').strip()
            obs = (request.form.get(f'ingrediente_obs_{linea_id}_{ing_id}') or '').strip()
            if not cantidad_txt and not obs:
                conn.execute("""
                    DELETE FROM produccion_ingredientes_reales
                    WHERE linea_produccion_id = ? AND receta_ingrediente_id = ?
                """, (linea_id, ing_id))
                continue

            linea = conn.execute("""
                SELECT * FROM lineas_produccion
                WHERE id = ? AND parte_id = ?
            """, (linea_id, parte_id)).fetchone()
            ing = conn.execute("""
                SELECT ri.*, p.id AS producto_id
                FROM recetas_ingredientes ri
                JOIN productos p ON p.id = ri.producto_id
                WHERE ri.id = ?
            """, (ing_id,)).fetchone()
            if not linea or not ing:
                continue

            cantidad_planificada = _cantidad_prevista_ingrediente(linea, ing["cantidad"])
            cantidad_real = _to_float(cantidad_txt, cantidad_planificada)
            conn.execute("""
                INSERT INTO produccion_ingredientes_reales (
                    linea_produccion_id, receta_ingrediente_id, producto_id,
                    cantidad_planificada, cantidad_real, observaciones,
                    usuario, empresa_id, fecha
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(linea_produccion_id, receta_ingrediente_id)
                DO UPDATE SET
                    producto_id = excluded.producto_id,
                    cantidad_planificada = excluded.cantidad_planificada,
                    cantidad_real = excluded.cantidad_real,
                    observaciones = excluded.observaciones,
                    usuario = excluded.usuario,
                    empresa_id = excluded.empresa_id,
                    fecha = datetime('now')
            """, (
                linea_id,
                ing_id,
                ing["producto_id"],
                cantidad_planificada,
                cantidad_real,
                obs,
                session.get('user_nombre'),
                empresa_id,
            ))
        
        productos_extra = request.form.getlist('producto_extra[]')
        cantidades_extra = request.form.getlist('cantidad_extra[]')
        motivos_extra = request.form.getlist('motivo_extra[]')
        
        for i in range(len(productos_extra)):
            if productos_extra[i] and cantidades_extra[i]:
                producto_centro_sql, producto_centro_params = _filtro_producto_visible_centro(conn, "p", centro_id)
                producto_extra = conn.execute(f"""
                    SELECT p.id
                    FROM productos p
                    WHERE p.id = ?
                      AND p.empresa_id = ?
                      AND COALESCE(p.activo, 1) = 1
                      {producto_centro_sql}
                """, [productos_extra[i], empresa_id] + producto_centro_params).fetchone()
                if not producto_extra:
                    continue
                conn.execute("""
                    INSERT INTO consumo_extra (parte_id, producto_id, cantidad, motivo, usuario, fecha)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                """, (parte_id, productos_extra[i], float(cantidades_extra[i]), 
                      motivos_extra[i], session.get('user_nombre')))
        
        conn.commit()
        conn.close()
        
        flash('✅ Parte actualizado con los ajustes del subdepartamento', 'success')
        return redirect(url_for('produccion.produccion_subdepartamento', subdepto_id=parte['subdepartamento_id'], fecha=parte['fecha']))
    
    lineas = conn.execute("""
        SELECT lp.*, r.nombre as receta_nombre, r.descripcion, r.alergenos,
               r.nombre_en, r.nombre_fr, r.nombre_de, r.nombre_it,
               pp.id AS parte_id, pp.fecha AS parte_fecha, pp.turno AS parte_turno,
               COALESCE(s.nombre, '') AS subdepartamento_nombre
        FROM lineas_produccion lp
        JOIN recetas r ON lp.receta_id = r.id
        JOIN partes_produccion pp ON pp.id = lp.parte_id
        LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
        WHERE lp.parte_id = ?
    """, (parte_id,)).fetchall()
    
    lineas_con_ingredientes = []
    
    for linea in lineas:
        control = conn.execute("""
            SELECT *
            FROM produccion_controles_appcc
            WHERE linea_produccion_id = ?
        """, (linea["id"],)).fetchone()
        lote_elaborado = conn.execute("""
            SELECT *
            FROM produccion_lotes_elaborados
            WHERE linea_produccion_id = ?
        """, (linea["id"],)).fetchone()
        ingredientes = conn.execute("""
            SELECT
                ri.id,
                p.id AS producto_id,
                p.nombre,
                ri.cantidad,
                p.unidad,
                p.familia,
                p.alergenos,
                p.alergenos_validado,
                p.tipo_conservacion,
                p.tiempo_descongelado_horas,
                pir.cantidad_real AS cantidad_real_registrada,
                pir.observaciones AS observaciones_registro,
                CASE WHEN io.id IS NULL THEN 0 ELSE 1 END AS omitido
            FROM recetas_ingredientes ri
            JOIN productos p ON ri.producto_id = p.id
            LEFT JOIN produccion_ingredientes_reales pir
                ON pir.receta_ingrediente_id = ri.id
               AND pir.linea_produccion_id = ?
            LEFT JOIN ingredientes_omitidos io
                ON io.receta_ingrediente_id = ri.id
               AND io.parte_id = ?
            WHERE ri.receta_id = ?
            ORDER BY p.nombre
        """, (linea['id'], parte_id, linea['receta_id'])).fetchall()
        ingredientes_detalle = []
        for ing in ingredientes:
            item = dict(ing)
            prevista = _cantidad_prevista_ingrediente(linea, ing["cantidad"])
            item["cantidad_prevista"] = prevista
            item["cantidad_real_sugerida"] = (
                ing["cantidad_real_registrada"]
                if ing["cantidad_real_registrada"] is not None
                else prevista
            )
            item["requiere_descongelado"] = _producto_requiere_alerta_descongelado(ing)
            item["stock_apto_descongelado"] = 0.0
            item["stock_en_descongelacion"] = 0.0
            item["stock_pendiente_congelador"] = 0.0
            if item["requiere_descongelado"]:
                lotes_aptos, _ = _lotes_disponibles_para_consumo(conn, ing, empresa_id, centro_id)
                item["stock_apto_descongelado"] = sum(
                    _to_float(l["cantidad_actual"], 0.0) for l in lotes_aptos
                )
                estado_stock = conn.execute("""
                    SELECT
                        COALESCE(SUM(CASE WHEN COALESCE(estado_descongelado, 'pendiente') = 'en_curso' THEN cantidad_actual ELSE 0 END), 0) AS en_curso,
                        COALESCE(SUM(CASE WHEN COALESCE(estado_descongelado, 'pendiente') = 'pendiente' THEN cantidad_actual ELSE 0 END), 0) AS pendiente
                    FROM lotes
                    WHERE producto_id = ?
                      AND empresa_id = ?
                      AND estado = 'activo'
                      AND COALESCE(cantidad_actual, 0) > 0
                """, (ing["producto_id"], empresa_id)).fetchone()
                item["stock_en_descongelacion"] = _to_float(estado_stock["en_curso"] if estado_stock else 0, 0.0)
                item["stock_pendiente_congelador"] = _to_float(estado_stock["pendiente"] if estado_stock else 0, 0.0)
            ingredientes_detalle.append(item)
        linea_dict = dict(linea)
        alergenos_operativos = _alergenos_linea_operativa(linea_dict, ingredientes_detalle)
        lineas_con_ingredientes.append({
            'linea': linea,
            'ingredientes': ingredientes_detalle,
            'control': control,
            'lote_elaborado': lote_elaborado,
            'alergenos_operativos': alergenos_operativos,
            'avisos_higiene': _avisos_higiene_alergenos(alergenos_operativos),
        })

    alergenos_parte = set()
    for item in lineas_con_ingredientes:
        alergenos_parte.update(_normalizar_texto(a) for a in _separar_alergenos(item["alergenos_operativos"]))
    for item in lineas_con_ingredientes:
        propios = {_normalizar_texto(a) for a in _separar_alergenos(item["alergenos_operativos"])}
        item["contaminacion_cruzada"] = bool(propios and (alergenos_parte - propios))
        item["avisos_higiene"] = _avisos_higiene_alergenos(
            item["alergenos_operativos"],
            item["contaminacion_cruzada"],
        )
    
    producto_centro_sql, producto_centro_params = _filtro_producto_visible_centro(conn, "p", centro_id)
    productos = conn.execute(f"""
        SELECT id, nombre, unidad
        FROM productos p
        WHERE activo = 1 AND empresa_id = ?
          {producto_centro_sql}
        ORDER BY nombre
    """, [empresa_id] + producto_centro_params).fetchall()
    
    conn.close()
    return render_template('produccion/gestionar_parte.html',
                         parte=parte,
                         lineas=lineas_con_ingredientes,
                         productos=productos,
                         datetime_local=_to_datetime_local)


@produccion_bp.route('/partes/<int:parte_id>/recogida-sobrantes', methods=['GET', 'POST'])
@login_required
def recogida_sobrantes_parte(parte_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_planificacion_schema(conn)
    conn.commit()

    scope_sql, scope_params = _scope_partes_produccion(conn, "pp", empresa_id)
    parte = conn.execute(f"""
        SELECT pp.*, md.fecha AS menu_fecha, t.nombre as tipo_nombre,
               s.nombre as subdepartamento_nombre,
               d.nombre as departamento_nombre
        FROM partes_produccion pp
        LEFT JOIN menus_diarios md ON pp.menu_diario_id = md.id
        LEFT JOIN tipos_menu t ON md.tipo_menu_id = t.id
        LEFT JOIN subdepartamentos s ON pp.subdepartamento_id = s.id
        LEFT JOIN departamentos d ON s.departamento_id = d.id
        WHERE pp.id = ? AND {scope_sql}
    """, [parte_id] + scope_params).fetchone()

    if not parte:
        conn.close()
        flash('Parte no encontrado', 'danger')
        return redirect(url_for('produccion.produccion_index'))

    lineas = conn.execute("""
        SELECT lp.*, r.nombre as receta_nombre
        FROM lineas_produccion lp
        JOIN recetas r ON lp.receta_id = r.id
        WHERE lp.parte_id = ?
        ORDER BY COALESCE(lp.estacion_buffet, ''), r.nombre
    """, (parte_id,)).fetchall()

    if request.method == 'POST':
        bloqueo = _requiere_gestion_produccion(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_produccion(conn)
        if bloqueo_centro:
            return bloqueo_centro
        for linea in lineas:
            hay_sobrante = bool(request.form.get(f"hay_sobrante_{linea['id']}"))
            _guardar_sobrante_linea(conn, parte, linea, empresa_id, limpiar=not hay_sobrante)

        conn.commit()
        conn.close()
        flash('Recogida de sobrantes guardada. Imprime las etiquetas de los sobrantes reaprovechables desde esta pantalla.', 'success')
        return redirect(url_for('produccion.recogida_sobrantes_parte', parte_id=parte_id))

    controles_rows = conn.execute("""
        SELECT *
        FROM produccion_controles_appcc
        WHERE parte_id = ?
    """, (parte_id,)).fetchall()
    controles = {row["linea_produccion_id"]: row for row in controles_rows}
    lotes_rows = conn.execute("""
        SELECT *
        FROM produccion_lotes_elaborados
        WHERE parte_id = ?
          AND estado = 'disponible'
    """, (parte_id,)).fetchall()
    lotes = {row["linea_produccion_id"]: row for row in lotes_rows}

    conn.close()
    return render_template(
        'produccion/recogida_sobrantes.html',
        parte=parte,
        lineas=lineas,
        controles=controles,
        lotes=lotes,
    )


@produccion_bp.route('/trazabilidad')
@login_required
def trazabilidad_produccion():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_planificacion_schema(conn)
    conn.commit()

    fecha_desde = request.args.get('fecha_desde') or datetime.now().strftime('%Y-%m-%d')
    fecha_hasta = request.args.get('fecha_hasta') or fecha_desde
    destino = (request.args.get('destino') or '').strip()
    metodo = (request.args.get('metodo') or '').strip()

    scope_sql, scope_params = _scope_partes_produccion(conn, "pp", empresa_id)
    where = [
        scope_sql,
        "date(pp.fecha) >= date(?)",
        "date(pp.fecha) <= date(?)",
    ]
    params = scope_params + [fecha_desde, fecha_hasta]
    if destino:
        where.append("COALESCE(pc.destino_sobrante, '') = ?")
        params.append(destino)
    if metodo:
        where.append("COALESCE(pc.metodo_conservacion, '') = ?")
        params.append(metodo)

    rows = conn.execute(f"""
        SELECT
            pc.*,
            pp.fecha AS parte_fecha,
            pp.turno,
            pp.estado AS parte_estado,
            r.nombre AS receta_nombre,
            COALESCE(s.nombre, pv.nombre, 'Sin subdepartamento') AS subdepartamento_nombre,
            ple.id AS lote_id,
            ple.codigo_lote,
            ple.estado AS lote_estado,
            ple.fecha_limite_uso AS lote_fecha_limite
        FROM produccion_controles_appcc pc
        JOIN partes_produccion pp ON pp.id = pc.parte_id
        JOIN lineas_produccion lp ON lp.id = pc.linea_produccion_id
        JOIN recetas r ON r.id = lp.receta_id
        LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
        LEFT JOIN puntos_venta pv ON pv.id = pp.punto_venta_id
        LEFT JOIN produccion_lotes_elaborados ple
          ON ple.linea_produccion_id = pc.linea_produccion_id
        WHERE {" AND ".join(where)}
        ORDER BY pp.fecha DESC, pp.turno, subdepartamento_nombre, r.nombre
    """, params).fetchall()

    resumen = {
        "registros": len(rows),
        "reaprovechamiento": sum(1 for r in rows if r["destino_sobrante"] == "reaprovechamiento"),
        "merma": sum(1 for r in rows if r["destino_sobrante"] in {"merma_buffet", "desecho"}),
        "regeneracion": sum(1 for r in rows if _to_int(r["requiere_regeneracion"], 0)),
        "lotes": sum(1 for r in rows if r["codigo_lote"]),
    }

    conn.close()
    return render_template(
        'produccion/trazabilidad.html',
        rows=rows,
        resumen=resumen,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        destino=destino,
        metodo=metodo,
    )


@produccion_bp.route('/lotes-elaborados/<int:lote_id>/destinar', methods=['POST'])
@login_required
def destinar_lote_sobrante(lote_id):
    conn = get_db()
    bloqueo = _requiere_gestion_produccion(conn) or _requiere_centro_produccion(conn)
    if bloqueo:
        return bloqueo
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_planificacion_schema(conn)
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    scope, params = _scope_partes_produccion(conn, 'pp', empresa_id)
    lote = conn.execute(f'''
        SELECT ple.* FROM produccion_lotes_elaborados ple
        JOIN partes_produccion pp ON pp.id=ple.parte_id
        WHERE ple.id=? AND ple.empresa_id=? AND {scope}
    ''', [lote_id, empresa_id] + params).fetchone()
    if not lote:
        conn.close()
        abort(404)
    try:
        _destinar_sobrante(conn, lote, request.form.get('cantidad'),
                          request.form.get('destino', ''), request.form.get('referencia', ''),
                          session.get('user_nombre') or 'Usuario')
        conn.commit()
        flash('Salida registrada y saldo del sobrante actualizado.', 'success')
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), 'warning')
    finally:
        conn.close()
    return redirect(url_for('produccion.recogida_sobrantes_parte', parte_id=lote['parte_id']))


@produccion_bp.route('/lotes-elaborados/<int:lote_id>/etiqueta')
@login_required
def etiqueta_lote_elaborado(lote_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_planificacion_schema(conn)
    formato_codigo = request.args.get('formato')
    device_uid = request.cookies.get('el_raco_device_uid')
    formatos_etiqueta = get_label_configs(conn, empresa_id, solo_activas=True)
    dispositivo_impresion = get_current_print_device(conn, empresa_id, device_uid)
    config_etiqueta = get_label_config(conn, empresa_id, formato_codigo, device_uid)
    conn.commit()

    scope_sql, scope_params = _scope_partes_produccion(conn, "pp", empresa_id)
    lote = conn.execute(f"""
        SELECT
            ple.*,
            r.nombre AS receta_nombre,
            r.descripcion AS receta_descripcion,
            pp.fecha AS parte_fecha,
            pp.turno,
            COALESCE(s.nombre, pv.nombre, 'Produccion') AS subdepartamento_nombre,
            pc.destino_sobrante,
            pc.cantidad_sobrante,
            pc.unidad_sobrante,
            pc.motivo_merma,
            e.nombre AS empresa_nombre,
            e.nif AS empresa_nif,
            e.logo AS empresa_logo
        FROM produccion_lotes_elaborados ple
        JOIN lineas_produccion lp ON lp.id = ple.linea_produccion_id
        JOIN partes_produccion pp ON pp.id = ple.parte_id
        JOIN recetas r ON r.id = ple.receta_id
        LEFT JOIN produccion_controles_appcc pc ON pc.linea_produccion_id = ple.linea_produccion_id
        LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
        LEFT JOIN puntos_venta pv ON pv.id = pp.punto_venta_id
        LEFT JOIN empresas e ON e.id = pp.empresa_id
        WHERE ple.id = ? AND ple.empresa_id = ?
          AND {scope_sql}
    """, [lote_id, empresa_id] + scope_params).fetchone()

    if not lote:
        conn.close()
        flash('Lote elaborado no encontrado', 'danger')
        return redirect(url_for('produccion.trazabilidad_produccion'))

    ingredientes_rows = conn.execute("""
        SELECT p.nombre, p.alergenos, p.alergenos_validado
        FROM produccion_ingredientes_reales pir
        JOIN productos p ON p.id = pir.producto_id
        WHERE pir.linea_produccion_id = ?
          AND COALESCE(pir.cantidad_real, 0) > 0
        ORDER BY p.nombre
    """, (lote["linea_produccion_id"],)).fetchall()

    if not ingredientes_rows:
        ingredientes_rows = conn.execute("""
            SELECT p.nombre, p.alergenos, p.alergenos_validado
            FROM recetas_ingredientes ri
            JOIN productos p ON p.id = ri.producto_id
            WHERE ri.receta_id = ?
            ORDER BY p.nombre
        """, (lote["receta_id"],)).fetchall()

    ingredientes = [row["nombre"] for row in ingredientes_rows]
    alergenos = _unir_alergenos(row["alergenos"] for row in ingredientes_rows if row["alergenos"])
    alergenos_pendiente = any(
        row["alergenos"] and not _to_int(row["alergenos_validado"], 0)
        for row in ingredientes_rows
    )

    conn.close()
    return render_template(
        'produccion/etiqueta_lote.html',
        lote=lote,
        ingredientes=", ".join(ingredientes),
        alergenos=", ".join(alergenos),
        alergenos_pendiente=alergenos_pendiente,
        now=datetime.now(),
        formatos_etiqueta=formatos_etiqueta,
        config_etiqueta=config_etiqueta,
        dispositivo_impresion=dispositivo_impresion,
        label_css=build_label_css(config_etiqueta),
        zpl_contenido=zpl_lote_elaborado(lote, ", ".join(ingredientes), ", ".join(alergenos), config_etiqueta),
    )

# ============================================
# TARJETAS DE BUFFET
# ============================================
@produccion_bp.route('/<int:parte_id>/tarjetas')
@login_required
def tarjetas_buffet(parte_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_planificacion_schema(conn)
    conn.commit()
    idiomas = _idiomas_tarjeta_desde_request()
    modo_tarjeta = request.args.get("modo") or "digital"
    mostrar_ingredientes = request.args.get("ingredientes") == "1"
    
    scope_sql, scope_params = _scope_partes_produccion(conn, "pp", empresa_id)
    parte = conn.execute(f"""
        SELECT pp.*, pv.nombre as punto_venta_nombre
        FROM partes_produccion pp
        LEFT JOIN puntos_venta pv ON pp.punto_venta_id = pv.id
        WHERE pp.id = ? AND {scope_sql}
    """, [parte_id] + scope_params).fetchone()
    
    if not parte:
        flash('Parte no encontrado', 'danger')
        return redirect(url_for('produccion.produccion_index'))
    
    lineas_rows = conn.execute("""
        SELECT lp.*, r.nombre as receta_nombre, r.descripcion, r.alergenos,
               pp.id AS parte_id, pp.fecha AS parte_fecha, pp.turno AS parte_turno,
               COALESCE(s.nombre, pv.nombre, '') AS subdepartamento_nombre
        FROM lineas_produccion lp
        JOIN recetas r ON lp.receta_id = r.id
        JOIN partes_produccion pp ON pp.id = lp.parte_id
        LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
        LEFT JOIN puntos_venta pv ON pv.id = pp.punto_venta_id
        WHERE lp.parte_id = ?
        ORDER BY COALESCE(lp.estacion_buffet, ''), r.nombre
    """, (parte_id,)).fetchall()

    lineas = _preparar_tarjetas_publicas(_preparar_lineas_tarjetas(conn, lineas_rows), idiomas)

    conn.close()
    return render_template('produccion/tarjetas_buffet.html',
                         parte=parte,
                         lineas=lineas,
                         pendientes_alergenos=sum(1 for l in lineas if l["alergenos_pendiente"]),
                         idiomas=idiomas,
                         modo_tarjeta=modo_tarjeta,
                         mostrar_ingredientes=mostrar_ingredientes,
                         now=datetime.now())


@produccion_bp.route('/tarjetas')
@login_required
def tarjetas_servicio():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_planificacion_schema(conn)
    conn.commit()
    fecha = request.args.get('fecha') or datetime.now().strftime('%Y-%m-%d')
    servicio = (request.args.get('servicio') or '').strip()
    pantalla = request.args.get('pantalla') == '1'
    idiomas = _idiomas_tarjeta_desde_request()
    modo_tarjeta = request.args.get("modo") or "digital"
    mostrar_ingredientes = request.args.get("ingredientes") == "1"
    centro_pp_sql, centro_pp_params = _filtro_centro_tabla(conn, "partes_produccion", "")

    servicios = conn.execute(f"""
        SELECT DISTINCT COALESCE(turno, '') AS turno
        FROM partes_produccion
        WHERE empresa_id = ? AND date(fecha) = date(?)
          {centro_pp_sql}
        ORDER BY turno
    """, [empresa_id, fecha] + centro_pp_params).fetchall()

    centro_pp_alias_sql, centro_pp_alias_params = _filtro_centro_tabla(conn, "partes_produccion", "pp")
    lineas_rows = conn.execute(f"""
        SELECT lp.*, r.nombre AS receta_nombre, r.descripcion, r.alergenos,
               r.nombre_en, r.nombre_fr, r.nombre_de, r.nombre_it,
               pp.id AS parte_id, pp.fecha AS parte_fecha, pp.turno AS parte_turno,
               COALESCE(s.nombre, pv.nombre, 'Sin estacion') AS subdepartamento_nombre
        FROM partes_produccion pp
        JOIN lineas_produccion lp ON lp.parte_id = pp.id
        JOIN recetas r ON r.id = lp.receta_id
        LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
        LEFT JOIN puntos_venta pv ON pv.id = pp.punto_venta_id
        WHERE pp.empresa_id = ?
          AND date(pp.fecha) = date(?)
          AND (? = '' OR COALESCE(pp.turno, '') = ?)
          {centro_pp_alias_sql}
        ORDER BY pp.turno, COALESCE(s.nombre, pv.nombre, ''),
                 COALESCE(lp.estacion_buffet, ''), r.nombre
    """, [empresa_id, fecha, servicio, servicio] + centro_pp_alias_params).fetchall()

    lineas = _preparar_tarjetas_publicas(_preparar_lineas_tarjetas(conn, lineas_rows), idiomas)

    grupos_map = {}
    for linea in lineas:
        clave = (
            linea.get("parte_turno") or "",
            linea.get("subdepartamento_nombre") or "Sin estacion",
            linea.get("estacion_buffet") or "Buffet",
        )
        grupo = grupos_map.setdefault(clave, {
            "servicio": clave[0],
            "subdepartamento": clave[1],
            "estacion": clave[2],
            "lineas": [],
        })
        grupo["lineas"].append(linea)

    grupos = sorted(
        grupos_map.values(),
        key=lambda g: (
            _normalizar_texto(g["servicio"]),
            _normalizar_texto(g["subdepartamento"]),
            _normalizar_texto(g["estacion"]),
        )
    )

    conn.close()
    return render_template(
        'produccion/tarjetas_servicio.html',
        fecha=fecha,
        servicio=servicio,
        servicios=servicios,
        pantalla=pantalla,
        idiomas=idiomas,
        modo_tarjeta=modo_tarjeta,
        mostrar_ingredientes=mostrar_ingredientes,
        grupos=grupos,
        lineas=lineas,
        pendientes_alergenos=sum(1 for l in lineas if l["alergenos_pendiente"]),
        now=datetime.now(),
    )

# ============================================
# PRODUCCIÓN SEMANAL (nuevas rutas)
# ============================================

@produccion_bp.route('/tarjetas/asignacion', methods=['GET', 'POST'])
@login_required
def asignacion_tarjetas_buffet():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_planificacion_schema(conn)
    conn.commit()
    fecha = request.values.get('fecha') or datetime.now().strftime('%Y-%m-%d')
    servicio = (request.values.get('servicio') or '').strip()
    centro_pp_sql, centro_pp_params = _filtro_centro_tabla(conn, "partes_produccion", "")
    centro_pp_alias_sql, centro_pp_alias_params = _filtro_centro_tabla(conn, "partes_produccion", "pp")

    if request.method == 'POST':
        bloqueo = _requiere_gestion_produccion(conn)
        if bloqueo:
            return bloqueo
        bloqueo_centro = _requiere_centro_produccion(conn)
        if bloqueo_centro:
            return bloqueo_centro
        for linea_id in request.form.getlist('linea_id[]'):
            posicion = (request.form.get(f'posicion_{linea_id}') or '').strip().upper()
            conn.execute(f"""
                UPDATE lineas_produccion
                SET estacion_buffet = ?
                WHERE id = ?
                  AND parte_id IN (
                      SELECT id FROM partes_produccion
                      WHERE empresa_id = ?
                        AND date(fecha) = date(?)
                        AND (? = '' OR COALESCE(turno, '') = ?)
                        {centro_pp_sql}
                  )
            """, [posicion, linea_id, empresa_id, fecha, servicio, servicio] + centro_pp_params)
        conn.commit()
        conn.close()
        flash('Asignacion de tarjetas actualizada.', 'success')
        return redirect(url_for('produccion.asignacion_tarjetas_buffet', fecha=fecha, servicio=servicio))

    servicios = conn.execute(f"""
        SELECT DISTINCT COALESCE(turno, '') AS turno
        FROM partes_produccion
        WHERE empresa_id = ? AND date(fecha) = date(?)
          {centro_pp_sql}
        ORDER BY turno
    """, [empresa_id, fecha] + centro_pp_params).fetchall()

    lineas = conn.execute(f"""
        SELECT lp.id, lp.estacion_buffet, lp.raciones_planificadas,
               r.nombre AS receta_nombre, r.nombre_en, r.nombre_fr, r.nombre_de, r.nombre_it,
               r.alergenos,
               pp.turno, pp.fecha, pp.id AS parte_id,
               COALESCE(s.nombre, pv.nombre, 'Sin zona') AS zona
        FROM partes_produccion pp
        JOIN lineas_produccion lp ON lp.parte_id = pp.id
        JOIN recetas r ON r.id = lp.receta_id
        LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
        LEFT JOIN puntos_venta pv ON pv.id = pp.punto_venta_id
        WHERE pp.empresa_id = ?
          AND date(pp.fecha) = date(?)
          AND (? = '' OR COALESCE(pp.turno, '') = ?)
          {centro_pp_alias_sql}
        ORDER BY COALESCE(lp.estacion_buffet, ''), zona, r.nombre
    """, [empresa_id, fecha, servicio, servicio] + centro_pp_alias_params).fetchall()

    usadas = {(l["estacion_buffet"] or "").strip().upper() for l in lineas if (l["estacion_buffet"] or "").strip()}
    posiciones = sorted(usadas)
    for prefijo, cantidad in (("PS", 12), ("FRIO", 8), ("POSTRES", 8)):
        for i in range(1, cantidad + 1):
            codigo = f"{prefijo}-{i:02d}"
            if codigo not in usadas:
                posiciones.append(codigo)

    conn.close()
    return render_template(
        'produccion/tarjetas_asignacion.html',
        fecha=fecha,
        servicio=servicio,
        servicios=servicios,
        lineas=lineas,
        posiciones=posiciones,
    )


@produccion_bp.route('/tarjeta-digital/<codigo>')
@login_required
def tarjeta_digital_buffet(codigo):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_planificacion_schema(conn)
    conn.commit()
    fecha = request.args.get('fecha') or datetime.now().strftime('%Y-%m-%d')
    servicio = (request.args.get('servicio') or '').strip()
    idiomas = _idiomas_tarjeta_desde_request()
    mostrar_ingredientes = request.args.get("ingredientes") == "1"
    codigo_norm = (codigo or '').strip().upper()

    lineas_rows = conn.execute("""
        SELECT lp.*, r.nombre AS receta_nombre, r.descripcion, r.alergenos,
               r.nombre_en, r.nombre_fr, r.nombre_de, r.nombre_it,
               pp.id AS parte_id, pp.fecha AS parte_fecha, pp.turno AS parte_turno,
               COALESCE(s.nombre, pv.nombre, 'Buffet') AS subdepartamento_nombre
        FROM partes_produccion pp
        JOIN lineas_produccion lp ON lp.parte_id = pp.id
        JOIN recetas r ON r.id = lp.receta_id
        LEFT JOIN subdepartamentos s ON s.id = pp.subdepartamento_id
        LEFT JOIN puntos_venta pv ON pv.id = pp.punto_venta_id
        WHERE pp.empresa_id = ?
          AND date(pp.fecha) = date(?)
          AND UPPER(COALESCE(lp.estacion_buffet, '')) = ?
          AND (? = '' OR COALESCE(pp.turno, '') = ?)
        ORDER BY pp.turno, r.nombre
        LIMIT 1
    """, (empresa_id, fecha, codigo_norm, servicio, servicio)).fetchall()

    lineas = _preparar_tarjetas_publicas(_preparar_lineas_tarjetas(conn, lineas_rows), idiomas)
    linea = lineas[0] if lineas else None
    conn.close()
    return render_template(
        'produccion/tarjeta_digital.html',
        codigo=codigo_norm,
        fecha=fecha,
        servicio=servicio,
        idiomas=idiomas,
        mostrar_ingredientes=mostrar_ingredientes,
        linea=linea,
        now=datetime.now(),
    )


@produccion_bp.route('/semanal')
@login_required
def vista_semanal_produccion():
    """Vista semanal para planificar producción"""
    from datetime import datetime, timedelta
    
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    fecha_str = request.args.get('fecha', '')
    if fecha_str:
        try:
            fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d')
        except:
            fecha_dt = datetime.now()
    else:
        fecha_dt = datetime.now()
    
    inicio_semana = fecha_dt - timedelta(days=fecha_dt.weekday())
    fin_semana = inicio_semana + timedelta(days=6)
    
    dias_semana = []
    for i in range(7):
        dia = inicio_semana + timedelta(days=i)
        dias_semana.append({
            'fecha': dia.strftime('%Y-%m-%d'),
            'nombre': dia.strftime('%A').capitalize()
        })
    
    centro_tipo_sql, centro_tipo_params = _filtro_centro_tabla(conn, "tipos_menu", "")
    tipos = conn.execute(f"""
        SELECT id, nombre FROM tipos_menu 
        WHERE activo = 1 AND empresa_id = ? 
          {centro_tipo_sql}
        ORDER BY nombre
    """, [empresa_id] + centro_tipo_params).fetchall()
    
    centro_sub_sql, centro_sub_params = _filtro_centro_tabla(conn, "subdepartamentos", "")
    subdeptos = conn.execute(f"""
        SELECT id, nombre FROM subdepartamentos 
        WHERE activo = 1 AND empresa_id = ? 
          {centro_sub_sql}
        ORDER BY nombre
    """, [empresa_id] + centro_sub_params).fetchall()
    
    # Obtener planificación existente
    planificacion = {}
    for dia in dias_semana:
        planificacion[dia['fecha']] = {}
        for tipo in tipos:
            centro_ps_sql, centro_ps_params = _filtro_centro_tabla(conn, "planificacion_servicios", "ps")
            menus_asignados = conn.execute(f"""
                SELECT ps.id, ps.menu_diario_id, ps.orden,
                    s.nombre as subdepartamento_nombre,
                    (SELECT COUNT(*) FROM menu_platos mp 
                     JOIN menu_subdepartamentos ms ON mp.menu_subdepartamento_id = ms.id
                     WHERE ms.menu_diario_id = md.id) as num_platos
                FROM planificacion_servicios ps
                JOIN menus_diarios md ON ps.menu_diario_id = md.id
                LEFT JOIN subdepartamentos s ON ps.subdepartamento_id = s.id
                WHERE ps.fecha = ? AND ps.tipo_menu_id = ? AND ps.empresa_id = ?
                  {centro_ps_sql}
                ORDER BY ps.orden
            """, [dia['fecha'], tipo['id'], empresa_id] + centro_ps_params).fetchall()
            planificacion[dia['fecha']][tipo['id']] = menus_asignados
    
    centro_md_sql, centro_md_params = _filtro_centro_tabla(conn, "menus_diarios", "md")
    menus_disponibles = conn.execute(f"""
        SELECT md.*, t.nombre as tipo_nombre,
            (SELECT COUNT(*) FROM menu_platos mp 
             JOIN menu_subdepartamentos ms ON mp.menu_subdepartamento_id = ms.id
             WHERE ms.menu_diario_id = md.id) as num_platos
        FROM menus_diarios md
        JOIN tipos_menu t ON md.tipo_menu_id = t.id
        WHERE md.empresa_id = ?
          {centro_md_sql}
        ORDER BY md.fecha DESC
    """, [empresa_id] + centro_md_params).fetchall()
    
    conn.close()
    
    return render_template('produccion/vista_semanal.html',
                         dias_semana=dias_semana,
                         tipos=tipos,
                         subdeptos=subdeptos,
                         planificacion=planificacion,
                         menus_disponibles=menus_disponibles,
                         semana_inicio=inicio_semana.strftime('%Y-%m-%d'),
                         semana_fin=fin_semana.strftime('%Y-%m-%d'))

@produccion_bp.route('/semanal/anadir', methods=['POST'])
@login_required
def anadir_menu_produccion_semanal():
    """Añade un menú a la planificación semanal de producción"""
    fecha = request.form.get('fecha')
    menu_diario_id = request.form.get('menu_diario_id')
    subdepartamento_id = request.form.get('subdepartamento_id') or None
    
    if not all([fecha, menu_diario_id]):
        flash('Faltan parámetros', 'danger')
        return redirect(url_for('produccion.vista_semanal_produccion', fecha=fecha))
    
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro
    _asegurar_planificacion_schema(conn)
    conn.commit()

    centro_md_sql, centro_md_params = _filtro_centro_tabla(conn, "menus_diarios", "md")
    centro_md_select = "md.centro_id" if "centro_id" in _columnas_tabla(conn.cursor(), "menus_diarios") else "NULL AS centro_id"
    menu = conn.execute(f"""
        SELECT md.id, md.tipo_menu_id, {centro_md_select}
        FROM menus_diarios md
        WHERE id = ? AND empresa_id = ?
          {centro_md_sql}
    """, [menu_diario_id, empresa_id] + centro_md_params).fetchone()

    if not menu:
        conn.close()
        flash('Menu no encontrado', 'danger')
        return redirect(url_for('produccion.vista_semanal_produccion', fecha=fecha))

    subdepartamento_id = int(subdepartamento_id) if subdepartamento_id else None
    if subdepartamento_id:
        sub_sql, sub_params = _filtro_centro_tabla(conn, "subdepartamentos", "s")
        sub_valido = conn.execute(f"""
            SELECT id
            FROM subdepartamentos s
            WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1
              {sub_sql}
        """, [subdepartamento_id, empresa_id] + sub_params).fetchone()
        if not sub_valido:
            conn.close()
            flash('Subdepartamento no valido para este centro', 'danger')
            return redirect(url_for('produccion.vista_semanal_produccion', fecha=fecha))

    conn.execute("""
        DELETE FROM planificacion_servicios
        WHERE fecha = ?
          AND tipo_menu_id = ?
          AND empresa_id = ?
          AND COALESCE(centro_id, 0) = COALESCE(?, 0)
          AND (
                (? IS NULL AND subdepartamento_id IS NULL)
                OR subdepartamento_id = ?
          )
    """, (fecha, menu["tipo_menu_id"], empresa_id, menu["centro_id"], subdepartamento_id, subdepartamento_id))
    
    conn.execute("""
        INSERT INTO planificacion_servicios 
        (fecha, tipo_menu_id, menu_diario_id, subdepartamento_id, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (fecha, menu["tipo_menu_id"], menu_diario_id, subdepartamento_id, empresa_id, menu["centro_id"]))
    
    conn.commit()
    conn.close()
    
    flash('Menú añadido a la planificación', 'success')
    return redirect(url_for('produccion.vista_semanal_produccion', fecha=fecha))

@produccion_bp.route('/semanal/quitar/<int:planificacion_id>', methods=['POST'])
@login_required
def quitar_menu_produccion_semanal(planificacion_id):
    """Quita un menú de la planificación semanal de producción"""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro
    
    _asegurar_planificacion_schema(conn)
    conn.commit()
    centro_ps_sql, centro_ps_params = _filtro_centro_tabla(conn, "planificacion_servicios", "")
    plan = conn.execute(f"""
        SELECT fecha FROM planificacion_servicios 
        WHERE id = ? AND empresa_id = ?
          {centro_ps_sql}
    """, [planificacion_id, empresa_id] + centro_ps_params).fetchone()
    
    if plan:
        conn.execute(
            f"DELETE FROM planificacion_servicios WHERE id = ? AND empresa_id = ? {centro_ps_sql}",
            [planificacion_id, empresa_id] + centro_ps_params,
        )
        conn.commit()
        flash('Menú eliminado de la planificación', 'success')
        fecha = plan['fecha']
    else:
        flash('No se encontró la planificación', 'danger')
        fecha = None
    
    conn.close()
    return redirect(url_for('produccion.vista_semanal_produccion', fecha=fecha))

@produccion_bp.route('/semanal/enviar', methods=['POST'])
@login_required
def enviar_semanal_a_produccion():
    """Crea partes de producción para todos los menús de un día/servicio"""
    fecha = request.form.get('fecha')
    tipo_menu_id = request.form.get('tipo_menu_id')
    
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro
    _asegurar_planificacion_schema(conn)
    conn.commit()
    
    centro_ps_sql, centro_ps_params = _filtro_centro_tabla(conn, "planificacion_servicios", "ps")
    menus = conn.execute(f"""
        SELECT md.id AS id, ps.id AS planificacion_id, ps.fecha AS fecha_servicio,
               ps.subdepartamento_id AS filtro_subdepartamento_id,
               md.*, t.nombre AS tipo_nombre
        FROM planificacion_servicios ps
        JOIN menus_diarios md ON ps.menu_diario_id = md.id
        JOIN tipos_menu t ON ps.tipo_menu_id = t.id
        WHERE ps.fecha = ? AND ps.tipo_menu_id = ? AND ps.empresa_id = ?
          {centro_ps_sql}
    """, [fecha, tipo_menu_id, empresa_id] + centro_ps_params).fetchall()
    
    partes_creados = 0
    for menu in menus:
        creados, _lineas = _crear_partes_buffet_desde_menu(
            conn,
            menu,
            empresa_id,
            fecha_servicio=menu["fecha_servicio"] or fecha,
            filtro_subdepartamento_id=menu["filtro_subdepartamento_id"],
            observaciones=f"Desde planificacion semanal {fecha}",
        )
        partes_creados += creados
        continue
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO partes_produccion 
            (fecha, turno, responsable, menu_diario_id, subdepartamento_id, estado, empresa_id)
            VALUES (?, ?, ?, ?, ?, 'planificado', ?)
        """, (fecha, 'Según servicio', session.get('user_nombre'), 
              menu['menu_diario_id'], menu['subdepartamento_id'], empresa_id))
        parte_id = cursor.lastrowid
        partes_creados += 1
        
        platos = conn.execute("""
            SELECT mp.receta_id
            FROM menu_platos mp
            JOIN menu_subdepartamentos ms ON mp.menu_subdepartamento_id = ms.id
            WHERE ms.menu_diario_id = ?
        """, (menu['menu_diario_id'],)).fetchall()
        
        for plato in platos:
            conn.execute("""
                INSERT INTO lineas_produccion (parte_id, receta_id, cantidad_planificada, estado)
                VALUES (?, ?, ?, 'pendiente')
            """, (parte_id, plato['receta_id'], 1))
    
    conn.commit()
    conn.close()
    
    flash(f'{partes_creados} partes de producción creados', 'success')
    return redirect(url_for('produccion.produccion_departamentos', fecha=fecha))

@produccion_bp.route('/semanal/toda', methods=['POST'])
@login_required
def planificar_semana_completa():
    """Crea partes de producción para toda la semana"""
    fecha_inicio = request.values.get('fecha_inicio')
    
    if not fecha_inicio:
        flash('No se especificó fecha de inicio', 'danger')
        return redirect(url_for('produccion.vista_semanal_produccion'))
    
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_produccion(conn)
    if bloqueo:
        return bloqueo
    bloqueo_centro = _requiere_centro_produccion(conn)
    if bloqueo_centro:
        return bloqueo_centro
    _asegurar_planificacion_schema(conn)
    conn.commit()
    
    fecha_inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
    fecha_fin_dt = fecha_inicio_dt + timedelta(days=6)
    fecha_fin = fecha_fin_dt.strftime('%Y-%m-%d')
    
    centro_ps_sql, centro_ps_params = _filtro_centro_tabla(conn, "planificacion_servicios", "ps")
    menus = conn.execute(f"""
        SELECT md.id AS id, ps.id AS planificacion_id, ps.fecha AS fecha_servicio,
               ps.subdepartamento_id AS filtro_subdepartamento_id,
               md.*, t.nombre AS tipo_nombre
        FROM planificacion_servicios ps
        JOIN menus_diarios md ON ps.menu_diario_id = md.id
        JOIN tipos_menu t ON ps.tipo_menu_id = t.id
        WHERE date(ps.fecha) BETWEEN date(?) AND date(?) AND ps.empresa_id = ?
          {centro_ps_sql}
    """, [fecha_inicio, fecha_fin, empresa_id] + centro_ps_params).fetchall()
    
    partes_creados = 0
    for menu in menus:
        creados, _lineas = _crear_partes_buffet_desde_menu(
            conn,
            menu,
            empresa_id,
            fecha_servicio=menu["fecha_servicio"],
            filtro_subdepartamento_id=menu["filtro_subdepartamento_id"],
            observaciones=f"Desde semana completa {fecha_inicio}",
        )
        partes_creados += creados
        continue
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO partes_produccion 
            (fecha, turno, responsable, menu_diario_id, subdepartamento_id, estado, empresa_id)
            VALUES (?, ?, ?, ?, ?, 'planificado', ?)
        """, (menu['fecha'], 'Según servicio', session.get('user_nombre'), 
              menu['menu_diario_id'], menu['subdepartamento_id'], empresa_id))
        parte_id = cursor.lastrowid
        partes_creados += 1
        
        platos = conn.execute("""
            SELECT mp.receta_id
            FROM menu_platos mp
            JOIN menu_subdepartamentos ms ON mp.menu_subdepartamento_id = ms.id
            WHERE ms.menu_diario_id = ?
        """, (menu['menu_diario_id'],)).fetchall()
        
        for plato in platos:
            conn.execute("""
                INSERT INTO lineas_produccion (parte_id, receta_id, cantidad_planificada, estado)
                VALUES (?, ?, ?, 'pendiente')
            """, (parte_id, plato['receta_id'], 1))
    
    conn.commit()
    conn.close()
    
    flash(f'{partes_creados} partes de producción creados para la semana', 'success')
    return redirect(url_for('produccion.produccion_departamentos', fecha=fecha_inicio))

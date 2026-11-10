# blueprints/lotes/routes.py
from flask import render_template, request, redirect, url_for, session, flash, jsonify
from database import get_db
from datetime import datetime, timedelta
from . import lotes_bp
from blueprints.auth.routes import login_required
from helpers.appcc_plan_helper import registrar_appcc_control
from helpers.etiquetas_helper import build_label_css, get_current_print_device, get_label_config, get_label_configs
from helpers.label_raw_helper import zpl_lote
from helpers.centros_helper import centro_activo_id, filtro_centro_por_departamento
import unicodedata


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


def _rol_key(valor):
    texto = unicodedata.normalize("NFKD", str(valor or "").strip().lower())
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    for separador in (" ", "-", "/", "."):
        texto = texto.replace(separador, "_")
    while "__" in texto:
        texto = texto.replace("__", "_")
    return texto.strip("_")


def _requiere_produccion_para_descongelado():
    if _rol_key(session.get("user_rol")) in ROLES_OPERATIVA_COCINA:
        return None
    flash("No tienes permisos para la operativa de congelados de produccion.", "warning")
    return redirect(url_for("home"))


def _columnas_tabla(cursor, tabla):
    try:
        return [row[1] for row in cursor.execute(f"PRAGMA table_info({tabla})").fetchall()]
    except Exception:
        return []


def _safe_add_column(cursor, tabla, definicion):
    nombre = definicion.strip().split()[0]
    if nombre not in _columnas_tabla(cursor, tabla):
        cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN {definicion}")


def _asegurar_lotes_schema(conn):
    cursor = conn.cursor()
    _safe_add_column(cursor, "productos", "tiempo_descongelado_horas INTEGER DEFAULT 0")
    _safe_add_column(cursor, "productos", "requiere_desinfeccion INTEGER DEFAULT 0")
    _safe_add_column(cursor, "productos", "departamento_responsable_id INTEGER")
    _safe_add_column(cursor, "productos", "control_lote INTEGER DEFAULT 1")
    _safe_add_column(cursor, "productos", "control_caducidad INTEGER DEFAULT 0")
    _safe_add_column(cursor, "lotes", "fecha_descongelado DATETIME")
    _safe_add_column(cursor, "lotes", "estado_descongelado TEXT DEFAULT 'pendiente'")
    _safe_add_column(cursor, "lotes", "fecha_ultimo_control DATETIME")
    _safe_add_column(cursor, "lotes", "temperatura_descongelado REAL")
    _safe_add_column(cursor, "lotes", "responsable_descongelado TEXT")
    _safe_add_column(cursor, "lotes", "observaciones_descongelado TEXT")
    _safe_add_column(cursor, "lotes", "responsable_recepcion TEXT")
    _safe_add_column(cursor, "lotes", "ubicacion_actual TEXT")
    _safe_add_column(cursor, "lotes", "incidencia_id INTEGER")
    _safe_add_column(cursor, "lotes", "documento_origen TEXT")
    _safe_add_column(cursor, "lotes", "notas_trazabilidad TEXT")
    _safe_add_column(cursor, "lotes", "origen_lote TEXT DEFAULT 'proveedor'")
    _safe_add_column(cursor, "lotes", "fecha_deterioro DATETIME")
    _safe_add_column(cursor, "lotes", "motivo_deterioro TEXT")
    _safe_add_column(cursor, "lotes", "almacen_id INTEGER")
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
        UPDATE lotes
        SET fecha_descongelado = NULL
        WHERE fecha_descongelado IS NOT NULL
          AND COALESCE(estado_descongelado, 'pendiente') = 'pendiente'
          AND NOT EXISTS (
              SELECT 1
              FROM registros_descongelado rd
              WHERE rd.lote_id = lotes.id
          )
    """)
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
        CREATE TABLE IF NOT EXISTS registros_deterioro_lotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            cantidad REAL NOT NULL,
            motivo TEXT,
            aspecto TEXT,
            olor TEXT,
            temperatura REAL,
            decision TEXT,
            destino TEXT,
            responsable TEXT,
            incidencia_id INTEGER,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (lote_id) REFERENCES lotes(id),
            FOREIGN KEY (incidencia_id) REFERENCES incidencias(id)
        )
    """)


def _to_float(valor, defecto=0.0):
    try:
        txt = str(valor if valor is not None else "").strip().replace(",", ".")
        return float(txt) if txt else defecto
    except Exception:
        return defecto


def _to_int_or_none(valor):
    try:
        txt = str(valor if valor is not None else "").strip()
        return int(txt) if txt else None
    except Exception:
        return None


def _normalizar_texto(valor):
    txt = str(valor or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in txt if not unicodedata.combining(c))


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


def _to_int(valor, defecto=0):
    try:
        txt = str(valor if valor is not None else "").strip()
        return int(float(txt.replace(",", "."))) if txt else defecto
    except Exception:
        return defecto


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


def _horas_descongelado(producto):
    if not _producto_es_congelado(producto) or _producto_se_sirve_congelado(producto):
        return 0
    horas = _to_int(producto["tiempo_descongelado_horas"] if "tiempo_descongelado_horas" in producto.keys() else 0, 0)
    if horas > 0:
        return horas
    texto = _normalizar_texto(
        f"{producto['familia'] if 'familia' in producto.keys() else ''} "
        f"{producto['nombre'] if 'nombre' in producto.keys() else ''}"
    )
    familia = _normalizar_texto(producto["familia"] if "familia" in producto.keys() else "")
    if familia in FAMILIAS_DESCONGELADO_LARGO or any(clave in texto for clave in CLAVES_DESCONGELADO_LARGO):
        return 72
    if any(clave in texto for clave in CLAVES_DESCONGELADO_MEDIO):
        return 24
    return 24


def _producto_requiere_alerta_descongelado(producto):
    return _horas_descongelado(producto) > 0


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

def _generar_lote_interno(producto_id):
    return f"INT-{producto_id}-{datetime.now().strftime('%Y%m%d%H%M%S%f')[:18]}"


def _requiere_desinfeccion(producto):
    if not producto:
        return False
    if int(producto["requiere_desinfeccion"] or 0) == 1:
        return True
    texto = f"{producto['familia'] or ''} {producto['nombre'] or ''}".lower()
    claves = ("fruta", "verdura", "vegetal", "lechuga", "tomate", "pepino", "pimiento")
    return any(clave in texto for clave in claves)

# ============================================
# LISTA DE LOTES
# ============================================
@lotes_bp.route('/')
@login_required
def lista_lotes():
    """Lista todos los lotes activos"""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_lotes_schema(conn)
    conn.commit()
    
    centro_sql, centro_params = filtro_centro_por_departamento(
        conn,
        "a",
        empresa_id,
        "almacenes",
        columnas_departamento=["departamento_id"],
        columnas_punto_venta=[],
    )
    lotes = conn.execute(f"""
        SELECT l.*, p.nombre as producto_nombre, p.unidad, p.familia,
               p.tipo_conservacion, p.requiere_desinfeccion,
               pr.nombre as proveedor_nombre,
               (julianday(l.fecha_caducidad) - julianday('now')) as dias_caducidad,
               (SELECT COUNT(*) FROM registros_desinfeccion rd WHERE rd.lote_id = l.id) as num_desinfecciones
        FROM lotes l
        JOIN productos p ON l.producto_id = p.id
        LEFT JOIN proveedores pr ON l.proveedor_id = pr.id
        LEFT JOIN almacenes a ON a.id = l.almacen_id
        WHERE l.estado = 'activo' AND l.empresa_id = ?
          {centro_sql}
        ORDER BY l.fecha_caducidad, l.fecha_descongelado
    """, [empresa_id] + centro_params).fetchall()
    
    conn.close()
    return render_template('lotes/lotes.html', lotes=lotes)

# ============================================
# NUEVO LOTE
# ============================================
@lotes_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_lote():
    """Registra un lote en recepción con control APPCC"""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_lotes_schema(conn)
    conn.commit()
    
    if request.method == 'POST':
        producto_id = request.form['producto_id']
        proveedor_id = request.form.get('proveedor_id')
        codigo_lote = (request.form.get('codigo_lote') or '').strip()
        fecha_caducidad = request.form.get('fecha_caducidad')
        cantidad = float(request.form['cantidad'])
        temperatura = float(request.form.get('temperatura', 0))
        ubicacion = request.form.get('ubicacion', '')
        producto = conn.execute("SELECT * FROM productos WHERE id = ? AND empresa_id = ?", (producto_id, empresa_id)).fetchone()
        notas = 'Requiere desinfeccion en recepcion antes de pasar a camara segura.' if _requiere_desinfeccion(producto) else ''
        origen_lote = 'proveedor'
        lote_proveedor = codigo_lote or None
        if not codigo_lote:
            codigo_lote = _generar_lote_interno(producto_id)
            origen_lote = 'interno'
            notas = (notas + ' | ' if notas else '') + 'Lote interno generado por la ERP al no informarse lote de proveedor.'
        
        # Insertar lote
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO lotes 
            (producto_id, proveedor_id, codigo_lote, fecha_caducidad, cantidad_inicial, cantidad_actual,
             temperatura_recepcion, responsable_recepcion, ubicacion_actual, estado, empresa_id,
             documento_origen, notas_trazabilidad, lote_proveedor, origen_lote)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'activo', ?, ?, ?, ?, ?)
        """, (producto_id, proveedor_id, codigo_lote, fecha_caducidad, cantidad,
              cantidad, temperatura, session.get('user_nombre'), ubicacion, empresa_id,
              'Recepcion manual', notas, lote_proveedor, origen_lote))
        
        lote_id = cursor.lastrowid
        registrar_appcc_control(
            conn,
            empresa_id,
            "REC-01",
            valor=f"{codigo_lote} | {producto['nombre'] if producto else producto_id} | {cantidad:g} | {temperatura:g} C",
            resultado="ok",
            usuario=session.get('user_nombre') or 'admin',
            observaciones=notas or "Recepcion de lote registrada",
            origen_tabla="lotes",
            origen_id=lote_id,
            codigo_operacion=f"recepcion_lote_{lote_id}",
        )
        
        # Si el producto es congelado y tiene fecha de producción asociada, programar alerta
        if producto and producto['tiempo_descongelado_horas'] and producto['tiempo_descongelado_horas'] > 0:
            # Por ahora solo guardamos, las alertas se generarán al planificar producción
            pass
        
        conn.commit()
        conn.close()
        
        flash(f' Lote {codigo_lote} registrado correctamente', 'success')
        return redirect(url_for('lotes.lista_lotes'))
    
    # GET: mostrar formulario
    productos = conn.execute("""
        SELECT p.*, d.nombre as departamento_nombre
        FROM productos p
        LEFT JOIN departamentos d ON p.departamento_responsable_id = d.id
        WHERE p.activo = 1 AND p.empresa_id = ?
        ORDER BY p.nombre
    """, (empresa_id,)).fetchall()
    
    proveedores = conn.execute("SELECT * FROM proveedores WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    conn.close()
    
    return render_template('lotes/nuevo_lote.html', 
                         productos=productos,
                         proveedores=proveedores,
                         now=datetime.now())

# ============================================
# CONTROL DE DESCONGELADO
# ============================================
@lotes_bp.route('/<int:lote_id>/iniciar_descongelado', methods=['POST'])
@login_required
def iniciar_descongelado(lote_id):
    """Registra la salida del congelador e inicia la trazabilidad APPCC."""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    usuario = session.get('user_nombre') or 'admin'
    _asegurar_lotes_schema(conn)
    conn.commit()

    lote = conn.execute("""
        SELECT l.*, p.nombre AS producto_nombre, p.unidad
        FROM lotes l
        JOIN productos p ON p.id = l.producto_id
        WHERE l.id = ? AND l.empresa_id = ?
    """, (lote_id, empresa_id)).fetchone()

    if not lote:
        conn.close()
        flash('Lote no encontrado.', 'danger')
        return redirect(url_for('lotes.alertas_descongelado'))

    siguiente = request.form.get('next') or url_for('lotes.panel_carnes_pescados')
    if '://' in siguiente or siguiente.startswith('//'):
        siguiente = url_for('lotes.panel_carnes_pescados')
    estado = (lote['estado_descongelado'] or 'pendiente').strip().lower()
    if estado in ('en_curso', 'apto', 'no_apto'):
        conn.close()
        flash('Este lote ya tiene el descongelado iniciado o cerrado.', 'warning')
        if request.form.get('volver_panel') == '1':
            return redirect(siguiente)
        return redirect(url_for('lotes.control_descongelado', lote_id=lote_id, next=siguiente))

    cantidad_actual = _to_float(lote['cantidad_actual'], 0.0)
    cantidad_raw = request.form.get('cantidad')
    cantidad = _to_float(cantidad_raw, 0.0 if cantidad_raw is not None else cantidad_actual)
    temperatura_txt = (request.form.get('temperatura') or '').strip()
    temperatura = _to_float(temperatura_txt, None)
    ubicacion_destino = (request.form.get('ubicacion_destino') or 'Camara de descongelacion').strip()
    observaciones = (request.form.get('observaciones') or '').strip()
    alerta_id = _to_int_or_none(request.form.get('alerta_id'))

    if cantidad <= 0:
        conn.close()
        flash('Indica una cantidad valida para sacar del congelador.', 'warning')
        return redirect(siguiente)
    if cantidad > cantidad_actual:
        conn.close()
        flash('No puedes sacar mas cantidad que la disponible en el lote.', 'warning')
        return redirect(siguiente)
    if temperatura is None:
        conn.close()
        flash('Indica la temperatura de salida del congelador.', 'warning')
        return redirect(siguiente)

    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    es_parcial = cantidad_actual - cantidad > 0.0001
    lote_descongelado_id = lote_id
    codigo_descongelado = lote['codigo_lote']
    cursor = conn.cursor()

    try:
        if es_parcial:
            cantidad_restante = round(cantidad_actual - cantidad, 6)
            codigo_descongelado = f"{lote['codigo_lote']}-DES-{datetime.now().strftime('%m%d%H%M%S')}"
            notas = (
                f"Sub-lote creado al sacar {cantidad:g} {lote['unidad'] or ''} "
                f"del lote {lote['codigo_lote']} para descongelado."
            )
            cursor.execute("""
                UPDATE lotes
                SET cantidad_actual = ?,
                    fecha_descongelado = NULL,
                    estado_descongelado = 'pendiente',
                    fecha_ultimo_control = NULL,
                    temperatura_descongelado = NULL,
                    responsable_descongelado = NULL,
                    observaciones_descongelado = NULL
                WHERE id = ?
            """, (cantidad_restante, lote_id))
            cursor.execute("""
                INSERT INTO lotes (
                    codigo_lote, producto_id, cantidad_inicial, cantidad_actual,
                    fecha_recepcion, fecha_caducidad, proveedor_id, almacen_id,
                    temperatura_recepcion, lote_proveedor, origen_lote, albaran,
                    estado, observaciones, empresa_id, responsable_recepcion,
                    fecha_descongelado, temperatura_descongelado, responsable_descongelado,
                    observaciones_descongelado, estado_descongelado, ubicacion_actual,
                    fecha_ultimo_control, documento_origen, notas_trazabilidad
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'activo', ?, ?, ?, ?, ?, ?, ?, 'en_curso', ?, ?, ?, ?)
            """, (
                codigo_descongelado,
                lote['producto_id'],
                cantidad,
                cantidad,
                lote['fecha_recepcion'],
                lote['fecha_caducidad'],
                lote['proveedor_id'],
                lote['almacen_id'],
                lote['temperatura_recepcion'],
                lote['lote_proveedor'],
                lote['origen_lote'],
                lote['albaran'],
                lote['observaciones'],
                empresa_id,
                lote['responsable_recepcion'],
                lote['fecha_descongelado'] or ahora,
                temperatura,
                usuario,
                observaciones,
                ubicacion_destino,
                ahora,
                f"Descongelado parcial de lote {lote['codigo_lote']}",
                notas,
            ))
            lote_descongelado_id = cursor.lastrowid
            cursor.execute("""
                INSERT INTO movimientos_lotes (
                    lote_id, tipo, cantidad, destino_tipo, usuario, observaciones, documento, empresa_id
                )
                VALUES (?, 'salida_parcial_descongelado', ?, ?, ?, ?, ?, ?)
            """, (
                lote_id,
                cantidad,
                ubicacion_destino,
                usuario,
                f"Salida parcial para sub-lote {codigo_descongelado}",
                'APPCC inicio descongelado',
                empresa_id,
            ))
        else:
            cursor.execute("""
                UPDATE lotes
                SET estado_descongelado = 'en_curso',
                    fecha_descongelado = COALESCE(fecha_descongelado, ?),
                    fecha_ultimo_control = ?,
                    temperatura_descongelado = ?,
                    responsable_descongelado = ?,
                    observaciones_descongelado = ?,
                    ubicacion_actual = ?
                WHERE id = ?
            """, (ahora, ahora, temperatura, usuario, observaciones, ubicacion_destino, lote_id))

        cursor.execute("""
            INSERT INTO registros_descongelado (
                lote_id, temperatura, aspecto, olfativo, decision, responsable, observaciones, empresa_id
            )
            VALUES (?, ?, 'inicio', 'inicio', 'inicio', ?, ?, ?)
        """, (
            lote_descongelado_id,
            temperatura,
            usuario,
            observaciones or f"Sacado del congelador hacia {ubicacion_destino}",
            empresa_id,
        ))
        cursor.execute("""
            INSERT INTO movimientos_lotes (
                lote_id, tipo, cantidad, destino_tipo, usuario, observaciones, documento, empresa_id
            )
            VALUES (?, 'inicio_descongelado', ?, ?, ?, ?, ?, ?)
        """, (
            lote_descongelado_id,
            cantidad,
            ubicacion_destino,
            usuario,
            f"Inicio descongelado. Temperatura salida {temperatura:g} C.",
            'APPCC inicio descongelado',
            empresa_id,
        ))
        registrar_appcc_control(
            conn,
            empresa_id,
            "DES-01",
            valor=f"{codigo_descongelado} | {cantidad:g} {lote['unidad'] or ''} | {temperatura:g} C | {ubicacion_destino}",
            resultado="ok",
            usuario=usuario,
            observaciones=observaciones or "Salida de congelador registrada",
            origen_tabla="lotes",
            origen_id=lote_descongelado_id,
            codigo_operacion=f"inicio_descongelado_{lote_descongelado_id}",
        )

        if alerta_id:
            cursor.execute("""
                UPDATE alertas_programadas
                SET enviada = 1, resuelta = 1
                WHERE id = ? AND empresa_id = ?
            """, (alerta_id, empresa_id))
        else:
            cursor.execute("""
                UPDATE alertas_programadas
                SET enviada = 1, resuelta = 1
                WHERE tipo = 'descongelado'
                  AND lote_id = ?
                  AND empresa_id = ?
                  AND COALESCE(resuelta, 0) = 0
            """, (lote_id, empresa_id))

        conn.commit()
        flash(f'{codigo_descongelado} marcado como sacado del congelador.', 'success')
        if request.form.get('volver_panel') == '1':
            return redirect(siguiente)
        return redirect(url_for('lotes.control_descongelado', lote_id=lote_descongelado_id, next=siguiente))
    except Exception as e:
        conn.rollback()
        flash(f'Error iniciando descongelado: {str(e)}', 'danger')
        return redirect(siguiente)
    finally:
        conn.close()


@lotes_bp.route('/<int:lote_id>/control_descongelado', methods=['GET', 'POST'])
@login_required
def control_descongelado(lote_id):
    """Registra un control durante el descongelado"""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    next_url = request.values.get('next') or url_for('lotes.panel_carnes_pescados')
    if '://' in next_url or next_url.startswith('//'):
        next_url = url_for('lotes.panel_carnes_pescados')
    _asegurar_lotes_schema(conn)
    conn.commit()
    
    # Obtener lote
    lote = conn.execute("""
        SELECT l.*, p.nombre as producto_nombre, p.unidad, p.tiempo_descongelado_horas,
               pr.nombre AS proveedor_nombre
        FROM lotes l
        JOIN productos p ON l.producto_id = p.id
        LEFT JOIN proveedores pr ON l.proveedor_id = pr.id
        WHERE l.id = ? AND l.empresa_id = ?
    """, (lote_id, empresa_id)).fetchone()
    
    if not lote:
        conn.close()
        flash(' Lote no encontrado', 'danger')
        return redirect(url_for('lotes.lista_lotes'))
    
    if request.method == 'POST':
        temperatura = float(request.form['temperatura'])
        aspecto = request.form.get('aspecto')
        olfativo = request.form.get('olfativo')
        decision = request.form['decision']
        observaciones = request.form.get('observaciones', '')
        
        # Registrar control
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO registros_descongelado 
            (lote_id, temperatura, aspecto, olfativo, decision, responsable, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (lote_id, temperatura, aspecto, olfativo, decision,
              session.get('user_nombre'), observaciones, empresa_id))
        registro_descongelado_id = cursor.lastrowid
        
        # Actualizar lote
        nuevo_estado = lote['estado_descongelado']
        if decision == 'apto':
            nuevo_estado = 'apto'
        elif decision == 'no_apto':
            nuevo_estado = 'no_apto'
        else:
            nuevo_estado = 'en_curso'
        
        conn.execute("""
            UPDATE lotes 
            SET temperatura_descongelado = ?,
                estado_descongelado = ?,
                fecha_ultimo_control = datetime('now')
            WHERE id = ?
        """, (temperatura, nuevo_estado, lote_id))
        conn.execute("""
            INSERT INTO movimientos_lotes (
                lote_id, tipo, cantidad, destino_tipo, usuario, observaciones, documento, empresa_id
            )
            VALUES (?, 'control_descongelado', 0, 'appcc', ?, ?, 'APPCC descongelado', ?)
        """, (
            lote_id,
            session.get('user_nombre') or 'admin',
            observaciones or f'Decision {decision}',
            empresa_id,
        ))
        resultado_appcc = "no_ok" if decision == "no_apto" else ("ok" if decision == "apto" else "pendiente")
        registro_appcc_id = registrar_appcc_control(
            conn,
            empresa_id,
            "DES-01",
            valor=f"{lote['codigo_lote']} | control {temperatura:g} C | {decision}",
            resultado=resultado_appcc,
            usuario=session.get('user_nombre') or 'admin',
            observaciones=observaciones or f"Control de descongelado: {decision}",
            accion_correctiva="Lote retenido/no apto" if decision == "no_apto" else "",
            origen_tabla="registros_descongelado",
            origen_id=registro_descongelado_id,
        )
        if decision == 'no_apto':
            incidencia = conn.execute(
                "SELECT incidencia_id FROM appcc_registros_plan WHERE id = ?",
                (registro_appcc_id,),
            ).fetchone()
            if incidencia and incidencia["incidencia_id"]:
                conn.execute("UPDATE lotes SET incidencia_id = ? WHERE id = ?", (incidencia["incidencia_id"], lote_id))
        conn.execute("""
            UPDATE alertas_programadas
            SET enviada = 1,
                resuelta = 1
            WHERE tipo = 'descongelado'
              AND lote_id = ?
              AND empresa_id = ?
              AND COALESCE(resuelta, 0) = 0
        """, (lote_id, empresa_id))
        
        conn.commit()
        conn.close()
        
        flash(f' Control registrado - Decisión: {decision}', 'success')
        siguiente = request.form.get('next') or next_url
        if '://' in siguiente or siguiente.startswith('//'):
            siguiente = url_for('lotes.lista_lotes')
        return redirect(siguiente)
    
    # GET: mostrar formulario
    # Obtener controles anteriores
    controles = conn.execute("""
        SELECT * FROM registros_descongelado
        WHERE lote_id = ?
        ORDER BY fecha_control DESC
    """, (lote_id,)).fetchall()

    desinfecciones = conn.execute("""
        SELECT *
        FROM registros_desinfeccion
        WHERE lote_id = ?
        ORDER BY fecha DESC
    """, (lote_id,)).fetchall()
    
    conn.close()
    return render_template('lotes/control_descongelado.html',
                         lote=lote,
                         controles=controles,
                         next_url=next_url,
                         volver_panel=next_url == url_for('lotes.panel_carnes_pescados'))


@lotes_bp.route('/<int:lote_id>/desinfeccion', methods=['GET', 'POST'])
@login_required
def desinfeccion_lote(lote_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_lotes_schema(conn)
    conn.commit()

    lote = conn.execute("""
        SELECT l.*, p.nombre AS producto_nombre, p.unidad, p.familia, p.requiere_desinfeccion
        FROM lotes l
        JOIN productos p ON p.id = l.producto_id
        WHERE l.id = ? AND l.empresa_id = ?
    """, (lote_id, empresa_id)).fetchone()
    if not lote:
        conn.close()
        flash('Lote no encontrado', 'danger')
        return redirect(url_for('lotes.lista_lotes'))

    if request.method == 'POST':
        producto_usado = request.form.get('producto_usado', '').strip()
        dosis = request.form.get('dosis', '').strip()
        tiempo_contacto = request.form.get('tiempo_contacto', '').strip()
        resultado = request.form.get('resultado', 'apto')
        observaciones = request.form.get('observaciones', '').strip()
        aclarado = 1 if request.form.get('aclarado') else 0
        responsable = session.get('user_nombre', 'admin')

        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO registros_desinfeccion (
                lote_id, producto_usado, dosis, tiempo_contacto, aclarado,
                resultado, responsable, observaciones, empresa_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            lote_id, producto_usado, dosis, tiempo_contacto, aclarado,
            resultado, responsable, observaciones, empresa_id
        ))
        registro_desinfeccion_id = cursor.lastrowid
        conn.execute("""
            INSERT INTO movimientos_lotes (
                lote_id, tipo, cantidad, destino_tipo, usuario, observaciones, documento, empresa_id
            )
            VALUES (?, 'desinfeccion', 0, 'camara_segura', ?, ?, 'APPCC desinfeccion', ?)
        """, (lote_id, responsable, observaciones or f'Resultado {resultado}', empresa_id))
        conn.execute("""
            UPDATE lotes
            SET notas_trazabilidad = TRIM(COALESCE(notas_trazabilidad, '') || ' | Desinfeccion: ' || ?)
            WHERE id = ? AND empresa_id = ?
        """, (resultado, lote_id, empresa_id))
        resultado_norm = _normalizar_texto(resultado)
        registrar_appcc_control(
            conn,
            empresa_id,
            "DESINF-01",
            valor=f"{lote['codigo_lote']} | {producto_usado or '-'} | dosis {dosis or '-'} | tiempo {tiempo_contacto or '-'}",
            resultado="ok" if resultado_norm in {"apto", "ok", "conforme"} else "no_ok",
            usuario=responsable,
            observaciones=observaciones or f"Resultado {resultado}",
            accion_correctiva="Bloquear/repetir desinfeccion" if resultado_norm not in {"apto", "ok", "conforme"} else "",
            origen_tabla="registros_desinfeccion",
            origen_id=registro_desinfeccion_id,
        )
        conn.commit()
        conn.close()
        flash('Desinfeccion registrada y trazada.', 'success')
        return redirect(url_for('lotes.historial_lote', lote_id=lote_id))

    registros = conn.execute("""
        SELECT *
        FROM registros_desinfeccion
        WHERE lote_id = ?
        ORDER BY fecha DESC
    """, (lote_id,)).fetchall()
    conn.close()
    return render_template('lotes/desinfeccion.html', lote=lote, registros=registros)


@lotes_bp.route('/<int:lote_id>/etiqueta')
@login_required
def etiqueta_lote(lote_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_lotes_schema(conn)
    formato_codigo = request.args.get('formato')
    device_uid = request.cookies.get('el_raco_device_uid')
    formatos_etiqueta = get_label_configs(conn, empresa_id, solo_activas=True)
    dispositivo_impresion = get_current_print_device(conn, empresa_id, device_uid)
    config_etiqueta = get_label_config(conn, empresa_id, formato_codigo, device_uid)
    conn.commit()

    lote = conn.execute("""
        SELECT l.*, p.nombre AS producto_nombre, p.unidad, p.familia, p.tipo_conservacion,
               pr.nombre AS proveedor_nombre,
               (SELECT COUNT(*) FROM registros_desinfeccion rd WHERE rd.lote_id = l.id) AS num_desinfecciones
        FROM lotes l
        JOIN productos p ON p.id = l.producto_id
        LEFT JOIN proveedores pr ON pr.id = l.proveedor_id
        WHERE l.id = ? AND l.empresa_id = ?
    """, (lote_id, empresa_id)).fetchone()
    if not lote:
        conn.close()
        flash('Lote no encontrado', 'danger')
        return redirect(url_for('lotes.lista_lotes'))
    conn.close()
    return render_template(
        'lotes/etiqueta_lote.html',
        lote=lote,
        now=datetime.now(),
        next_url=request.args.get('next') or url_for('lotes.lista_lotes'),
        formatos_etiqueta=formatos_etiqueta,
        config_etiqueta=config_etiqueta,
        dispositivo_impresion=dispositivo_impresion,
        label_css=build_label_css(config_etiqueta),
        zpl_contenido=zpl_lote(lote, config_etiqueta),
    )


@lotes_bp.route('/<int:lote_id>/deterioro', methods=['GET', 'POST'])
@login_required
def deterioro_lote(lote_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_lotes_schema(conn)
    conn.commit()

    lote = conn.execute("""
        SELECT l.*, p.nombre AS producto_nombre, p.unidad, p.familia, p.tipo_conservacion,
               pr.nombre AS proveedor_nombre
        FROM lotes l
        JOIN productos p ON p.id = l.producto_id
        LEFT JOIN proveedores pr ON pr.id = l.proveedor_id
        WHERE l.id = ? AND l.empresa_id = ?
    """, (lote_id, empresa_id)).fetchone()

    if not lote:
        conn.close()
        flash('Lote no encontrado', 'danger')
        return redirect(url_for('lotes.lista_lotes'))

    if request.method == 'POST':
        cantidad = _to_float(request.form.get('cantidad'), 0.0)
        cantidad_actual = _to_float(lote['cantidad_actual'], 0.0)
        if cantidad <= 0 or cantidad > cantidad_actual:
            conn.close()
            flash('La cantidad deteriorada no es valida.', 'warning')
            return redirect(url_for('lotes.deterioro_lote', lote_id=lote_id))

        motivo = (request.form.get('motivo') or '').strip() or 'Deterioro en almacenamiento'
        aspecto = (request.form.get('aspecto') or '').strip()
        olor = (request.form.get('olor') or '').strip()
        temperatura = _to_float(request.form.get('temperatura'), None)
        decision = request.form.get('decision') or 'desechar'
        destino = request.form.get('destino') or 'residuo_organico'
        observaciones = (request.form.get('observaciones') or '').strip()
        usuario = session.get('user_nombre') or 'admin'

        descripcion = (
            f"Deterioro lote {lote['codigo_lote']} - {lote['producto_nombre']}. "
            f"Cantidad: {cantidad} {lote['unidad'] or ''}. Motivo: {motivo}."
        )
        if observaciones:
            descripcion += f" Observaciones: {observaciones}"

        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO incidencias (tipo, referencia_id, usuario, descripcion, gravedad, estado, empresa_id)
            VALUES ('appcc', ?, ?, ?, ?, 'abierta', ?)
        """, (
            lote_id,
            usuario,
            descripcion,
            'alta' if decision in {'desechar', 'bloquear'} else 'media',
            empresa_id,
        ))
        incidencia_id = cursor.lastrowid

        cursor.execute("""
            INSERT INTO registros_deterioro_lotes (
                lote_id, cantidad, motivo, aspecto, olor, temperatura,
                decision, destino, responsable, incidencia_id, observaciones, empresa_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            lote_id, cantidad, motivo, aspecto, olor, temperatura,
            decision, destino, usuario, incidencia_id, observaciones, empresa_id,
        ))
        registro_deterioro_id = cursor.lastrowid
        registrar_appcc_control(
            conn,
            empresa_id,
            "MERMA-01",
            valor=f"{lote['codigo_lote']} | {cantidad:g} {lote['unidad'] or ''} | {decision} | {destino}",
            resultado="no_ok",
            usuario=usuario,
            observaciones=descripcion,
            accion_correctiva=decision,
            origen_tabla="registros_deterioro_lotes",
            origen_id=registro_deterioro_id,
            incidencia_id=incidencia_id,
        )

        nueva_cantidad = max(cantidad_actual - cantidad, 0)
        nuevo_estado = 'deteriorado' if nueva_cantidad <= 0 else lote['estado']
        cursor.execute("""
            UPDATE lotes
            SET cantidad_actual = ?,
                estado = ?,
                fecha_deterioro = datetime('now'),
                motivo_deterioro = ?,
                incidencia_id = ?,
                notas_trazabilidad = TRIM(COALESCE(notas_trazabilidad, '') || ' | Deterioro: ' || ?)
            WHERE id = ? AND empresa_id = ?
        """, (
            nueva_cantidad,
            nuevo_estado,
            motivo,
            incidencia_id,
            motivo,
            lote_id,
            empresa_id,
        ))

        cursor.execute("""
            INSERT INTO movimientos_lotes (
                lote_id, tipo, cantidad, destino_tipo, usuario, observaciones, documento, empresa_id
            )
            VALUES (?, 'deterioro', ?, ?, ?, ?, ?, ?)
        """, (
            lote_id,
            cantidad,
            destino,
            usuario,
            observaciones or motivo,
            f"Incidencia APPCC {incidencia_id}",
            empresa_id,
        ))

        cursor.execute("""
            INSERT INTO mermas (
                producto_id, lote_id, cantidad, motivo, responsable, observaciones, destino, empresa_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            lote['producto_id'],
            lote_id,
            cantidad,
            motivo,
            usuario,
            observaciones,
            destino,
            empresa_id,
        ))

        if lote['almacen_id']:
            cursor.execute("""
                UPDATE stock_almacen
                SET cantidad = MAX(COALESCE(cantidad, 0) - ?, 0)
                WHERE producto_id = ? AND almacen_id = ?
            """, (cantidad, lote['producto_id'], lote['almacen_id']))
            cursor.execute("""
                INSERT INTO movimientos_stock (producto_id, almacen_id, tipo, cantidad, motivo, usuario, documento, empresa_id)
                VALUES (?, ?, 'salida', ?, ?, ?, ?, ?)
            """, (
                lote['producto_id'],
                lote['almacen_id'],
                cantidad,
                f"Deterioro lote {lote['codigo_lote']}",
                usuario,
                f"Incidencia APPCC {incidencia_id}",
                empresa_id,
            ))

        conn.commit()
        conn.close()
        flash('Deterioro registrado: merma, movimiento e incidencia APPCC creados.', 'success')
        return redirect(url_for('lotes.historial_lote', lote_id=lote_id))

    conn.close()
    return render_template('lotes/deterioro_lote.html', lote=lote)

# ============================================
# ALERTAS DE DESCONGELADO
# ============================================
@lotes_bp.route('/carnes-pescados')
@login_required
def panel_carnes_pescados():
    """Pantalla operativa para sacar, controlar y etiquetar congelados de produccion."""
    bloqueo = _requiere_produccion_para_descongelado()
    if bloqueo:
        return bloqueo
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_lotes_schema(conn)
    conn.commit()
    filtro_descongelado = _sql_producto_alerta_descongelado("p")
    buscar = (request.args.get('q') or '').strip()
    departamento_id = request.args.get('departamento_id') or ''
    subdepartamento_id = request.args.get('subdepartamento_id') or ''
    almacen_id = request.args.get('almacen_id') or ''
    busqueda_sql = ""
    params_busqueda = []
    if buscar:
        busqueda_sql = " AND (p.nombre LIKE ? OR l.codigo_lote LIKE ?)"
        params_busqueda = [f"%{buscar}%", f"%{buscar}%"]
    scope_sql = ""
    scope_params = []
    if departamento_id.isdigit():
        scope_sql += " AND (p.departamento_responsable_id = ? OR al.departamento_id = ?)"
        scope_params.extend([int(departamento_id), int(departamento_id)])
    if subdepartamento_id.isdigit():
        scope_sql += " AND al.subdepartamento_id = ?"
        scope_params.append(int(subdepartamento_id))
    if almacen_id.isdigit():
        scope_sql += " AND l.almacen_id = ?"
        scope_params.append(int(almacen_id))

    departamentos = conn.execute(
        "SELECT id, nombre FROM departamentos WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        (empresa_id,),
    ).fetchall()
    subdepartamentos = conn.execute(
        "SELECT id, nombre, departamento_id FROM subdepartamentos WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        (empresa_id,),
    ).fetchall()
    almacenes = conn.execute(
        "SELECT id, nombre, departamento_id, subdepartamento_id FROM almacenes WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        (empresa_id,),
    ).fetchall()

    alertas = conn.execute(f"""
        SELECT a.id,
               a.lote_id,
               a.fecha_alerta,
               a.fecha_produccion,
               COALESCE(a.cantidad_necesaria, 0) AS cantidad_necesaria,
               l.codigo_lote, l.cantidad_actual, l.estado_descongelado,
               CASE
                   WHEN COALESCE(a.cantidad_necesaria, 0) > 0
                   THEN MIN(COALESCE(a.cantidad_necesaria, 0), COALESCE(l.cantidad_actual, 0))
                   ELSE COALESCE(l.cantidad_actual, 0)
               END AS cantidad_sugerida,
               p.nombre AS producto_nombre, p.unidad,
               pr.nombre AS proveedor_nombre,
               (julianday(a.fecha_alerta) - julianday('now')) * 24 AS horas_restantes
        FROM alertas_programadas a
        JOIN lotes l ON a.lote_id = l.id
        JOIN productos p ON l.producto_id = p.id
        LEFT JOIN almacenes al ON al.id = l.almacen_id
        LEFT JOIN proveedores pr ON l.proveedor_id = pr.id
        WHERE COALESCE(a.enviada, 0) = 0
          AND COALESCE(a.resuelta, 0) = 0
          AND a.tipo = 'descongelado'
          AND a.fecha_alerta <= datetime('now', '+7 days')
          AND l.empresa_id = ?
          AND COALESCE(l.estado_descongelado, 'pendiente') = 'pendiente'
          AND l.fecha_descongelado IS NULL
          AND {filtro_descongelado}
          {scope_sql}
          {busqueda_sql}
        ORDER BY a.fecha_alerta, p.nombre, l.codigo_lote
    """, [empresa_id] + scope_params + params_busqueda).fetchall()

    camara = conn.execute(f"""
        SELECT l.*, p.nombre AS producto_nombre, p.unidad,
               pr.nombre AS proveedor_nombre,
               (SELECT MAX(rd.fecha_control) FROM registros_descongelado rd WHERE rd.lote_id = l.id) AS ultimo_control
        FROM lotes l
        JOIN productos p ON p.id = l.producto_id
        LEFT JOIN almacenes al ON al.id = l.almacen_id
        LEFT JOIN proveedores pr ON pr.id = l.proveedor_id
        WHERE l.estado = 'activo'
          AND l.empresa_id = ?
          AND (
              COALESCE(l.estado_descongelado, '') IN ('en_curso', 'apto')
              OR LOWER(COALESCE(l.ubicacion_actual, '')) LIKE '%descongel%'
          )
          AND {filtro_descongelado}
          {scope_sql}
          {busqueda_sql}
        ORDER BY
          CASE COALESCE(l.estado_descongelado, '')
            WHEN 'en_curso' THEN 0
            WHEN 'apto' THEN 1
            ELSE 2
          END,
          l.fecha_descongelado IS NULL, l.fecha_descongelado, p.nombre
    """, [empresa_id] + scope_params + params_busqueda).fetchall()

    params = [empresa_id] + scope_params
    if buscar:
        params.extend([f"%{buscar}%", f"%{buscar}%"])

    disponibles = conn.execute(f"""
        SELECT l.*, p.nombre AS producto_nombre, p.unidad,
               pr.nombre AS proveedor_nombre,
               COALESCE(NULLIF(p.tiempo_descongelado_horas, 0), 72) AS tiempo_descongelado_horas
        FROM lotes l
        JOIN productos p ON p.id = l.producto_id
        LEFT JOIN almacenes al ON al.id = l.almacen_id
        LEFT JOIN proveedores pr ON pr.id = l.proveedor_id
        WHERE l.estado = 'activo'
          AND l.empresa_id = ?
          AND COALESCE(l.cantidad_actual, 0) > 0
          AND COALESCE(l.estado_descongelado, 'pendiente') = 'pendiente'
          AND l.fecha_descongelado IS NULL
          AND {filtro_descongelado}
          {scope_sql}
          {busqueda_sql}
        ORDER BY l.fecha_caducidad IS NULL, l.fecha_caducidad, p.nombre
        LIMIT 80
    """, params).fetchall()

    conn.close()
    return render_template(
        'lotes/carnes_pescados.html',
        alertas=alertas,
        en_curso=camara,
        disponibles=disponibles,
        buscar=buscar,
        departamento_id=departamento_id,
        subdepartamento_id=subdepartamento_id,
        almacen_id=almacen_id,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        almacenes=almacenes,
        now=datetime.now(),
    )


@lotes_bp.route('/alertas')
@login_required
def alertas_descongelado():
    """Muestra alertas de productos que deben descongelarse"""
    bloqueo = _requiere_produccion_para_descongelado()
    if bloqueo:
        return bloqueo
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_lotes_schema(conn)
    conn.commit()
    filtro_descongelado = _sql_producto_alerta_descongelado("p")
    centro_sql, centro_params = filtro_centro_por_departamento(
        conn,
        "al",
        empresa_id,
        "almacenes",
        columnas_departamento=["departamento_id"],
        columnas_punto_venta=[],
    )
    
    # Alertas programadas (72h, 48h, 24h antes)
    alertas = conn.execute(f"""
        SELECT a.*, l.codigo_lote, l.cantidad_actual, l.estado_descongelado,
               CASE
                   WHEN COALESCE(a.cantidad_necesaria, 0) > 0
                   THEN MIN(COALESCE(a.cantidad_necesaria, 0), COALESCE(l.cantidad_actual, 0))
                   ELSE COALESCE(l.cantidad_actual, 0)
               END AS cantidad_sugerida,
               p.nombre as producto_nombre, p.unidad,
               pr.nombre as proveedor_nombre,
               (julianday(a.fecha_alerta) - julianday('now')) * 24 as horas_restantes
        FROM alertas_programadas a
        JOIN lotes l ON a.lote_id = l.id
        JOIN productos p ON l.producto_id = p.id
        LEFT JOIN almacenes al ON al.id = l.almacen_id
        LEFT JOIN proveedores pr ON l.proveedor_id = pr.id
        WHERE a.enviada = 0 
          AND a.fecha_alerta <= datetime('now', '+7 days')
          AND a.tipo = 'descongelado'
          AND COALESCE(a.resuelta, 0) = 0
          AND l.empresa_id = ?
          AND COALESCE(l.estado_descongelado, 'pendiente') = 'pendiente'
          AND l.fecha_descongelado IS NULL
          AND {filtro_descongelado}
          {centro_sql}
        ORDER BY a.fecha_alerta
    """, [empresa_id] + centro_params).fetchall()
    
    # Lotes que deberían estar descongelándose pero no tienen alerta
    pendientes = conn.execute(f"""
        SELECT l.*, p.nombre as producto_nombre, p.unidad,
               COALESCE(NULLIF(p.tiempo_descongelado_horas, 0), 72) AS tiempo_descongelado_horas
        FROM lotes l
        JOIN productos p ON l.producto_id = p.id
        LEFT JOIN almacenes al ON al.id = l.almacen_id
        WHERE l.estado = 'activo'
          AND l.fecha_descongelado IS NULL
          AND l.empresa_id = ?
          AND {filtro_descongelado}
          {centro_sql}
    """, [empresa_id] + centro_params).fetchall()

    fecha_desde = datetime.now().strftime('%Y-%m-%d')
    fecha_hasta = (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')
    necesidades_sin_lote = []
    try:
        from blueprints.produccion.routes import _asegurar_planificacion_schema, _calcular_necesidades_planificacion
        _asegurar_planificacion_schema(conn)
        datos_plan = _calcular_necesidades_planificacion(conn, empresa_id, fecha_desde, fecha_hasta)
        agrupadas = {}
        for alerta in datos_plan.get("alertas_descongelado", []):
            producto_id = alerta.get("producto_id")
            if not producto_id:
                continue
            stock = conn.execute(f"""
                SELECT
                    COALESCE(SUM(CASE
                        WHEN COALESCE(estado, 'activo') = 'activo'
                         AND COALESCE(estado_descongelado, 'pendiente') = 'pendiente'
                         AND fecha_descongelado IS NULL
                        THEN COALESCE(cantidad_actual, 0) ELSE 0 END), 0) AS pendiente,
                    COALESCE(SUM(CASE
                        WHEN COALESCE(estado, 'activo') = 'activo'
                         AND COALESCE(estado_descongelado, 'pendiente') = 'apto'
                        THEN COALESCE(cantidad_actual, 0) ELSE 0 END), 0) AS apto
                FROM lotes
                LEFT JOIN almacenes al ON al.id = lotes.almacen_id
                WHERE empresa_id = ?
                  AND producto_id = ?
                  AND COALESCE(cantidad_actual, 0) > 0
                  {centro_sql}
            """, [empresa_id, producto_id] + centro_params).fetchone()
            if stock and _to_float(stock["pendiente"], 0.0) > 0:
                continue

            item = agrupadas.setdefault(producto_id, {
                "producto": alerta.get("producto") or "",
                "unidad": alerta.get("unidad") or "",
                "cantidad": 0.0,
                "desde": alerta.get("fecha_produccion"),
                "hasta": alerta.get("fecha_produccion"),
                "stock_apto": _to_float(stock["apto"], 0.0) if stock else 0.0,
            })
            item["cantidad"] += _to_float(alerta.get("cantidad"), 0.0)
            fecha_prod = alerta.get("fecha_produccion")
            if fecha_prod:
                item["desde"] = min(item["desde"] or fecha_prod, fecha_prod)
                item["hasta"] = max(item["hasta"] or fecha_prod, fecha_prod)
        necesidades_sin_lote = sorted(
            agrupadas.values(),
            key=lambda item: (item["stock_apto"] <= 0, item["producto"].lower())
        )
    except Exception:
        necesidades_sin_lote = []
    
    conn.close()
    
    return render_template('lotes/alertas.html',
                         alertas=alertas,
                         pendientes=pendientes,
                         necesidades_sin_lote=necesidades_sin_lote,
                         fecha_desde=fecha_desde,
                         fecha_hasta=fecha_hasta)

# ============================================
# API DE ALERTAS (para el contador del menú)
# ============================================
@lotes_bp.route('/api/alertas/descongelados')
@login_required
def api_alertas_descongelados():
    """API para contar alertas de descongelado pendientes"""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    filtro_descongelado = _sql_producto_alerta_descongelado("p")
    centro_sql, centro_params = filtro_centro_por_departamento(
        conn,
        "al",
        empresa_id,
        "almacenes",
        columnas_departamento=["departamento_id"],
        columnas_punto_venta=[],
    )
    
    total = conn.execute(f"""
        SELECT COUNT(*) as total 
        FROM alertas_programadas a
        JOIN lotes l ON a.lote_id = l.id
        JOIN productos p ON l.producto_id = p.id
        LEFT JOIN almacenes al ON al.id = l.almacen_id
        WHERE COALESCE(a.enviada, 0) = 0
          AND COALESCE(a.resuelta, 0) = 0
          AND a.tipo = 'descongelado'
          AND a.fecha_alerta <= datetime('now', '+7 days')
          AND a.empresa_id = ?
          AND {filtro_descongelado}
          {centro_sql}
    """, [empresa_id] + centro_params).fetchone()['total'] or 0
    
    conn.close()
    return {'total': total}

# ============================================
# GENERAR ALERTAS DE DESCONGELADO (API)
# ============================================
@lotes_bp.route('/api/generar_alertas_descongelado', methods=['POST'])
@login_required
def generar_alertas_descongelado():
    """API para generar alertas a partir de una fecha de producción"""
    fecha_produccion = request.json.get('fecha')
    if not fecha_produccion:
        return {'error': 'Fecha requerida'}, 400
    
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    # Buscar partes de producción para esa fecha
    centro_id = centro_activo_id()
    partes = conn.execute(f"""
        SELECT pp.*, lp.receta_id, lp.cantidad_planificada
        FROM partes_produccion pp
        JOIN lineas_produccion lp ON pp.id = lp.parte_id
        WHERE pp.fecha = ? AND pp.empresa_id = ?
          {"AND pp.centro_id = ?" if centro_id else ""}
    """, [fecha_produccion, empresa_id] + ([centro_id] if centro_id else [])).fetchall()
    
    alertas_generadas = 0
    for parte in partes:
        # Obtener ingredientes de la receta
        ingredientes = conn.execute("""
            SELECT ri.*,
                   p.nombre, p.familia, p.tipo_conservacion,
                   p.tiempo_descongelado_horas,
                   p.departamento_responsable_id
            FROM recetas_ingredientes ri
            JOIN productos p ON ri.producto_id = p.id
            WHERE ri.receta_id = ?
        """, (parte['receta_id'],)).fetchall()
        
        for ing in ingredientes:
            if not _producto_requiere_alerta_descongelado(ing):
                continue
            horas_descongelado = _horas_descongelado(ing)
            # Calcular fecha de alerta (fecha producción - tiempo descongelado)
            fecha_prod = datetime.strptime(fecha_produccion, '%Y-%m-%d')
            fecha_alerta = fecha_prod - timedelta(hours=horas_descongelado)
            
            # Buscar lotes disponibles de ese producto (FIFO)
            lotes = conn.execute("""
                SELECT * FROM lotes
                WHERE producto_id = ?
                  AND estado = 'activo'
                  AND fecha_descongelado IS NULL
                  AND cantidad_actual > 0
                  AND empresa_id = ?
                ORDER BY fecha_caducidad
            """, (ing['producto_id'], empresa_id)).fetchall()
            
            for lote in lotes[:1]:  # Solo el primer lote por ahora
                existe = conn.execute("""
                    SELECT id
                    FROM alertas_programadas
                    WHERE tipo = 'descongelado'
                      AND lote_id = ?
                      AND fecha_produccion = ?
                      AND empresa_id = ?
                    LIMIT 1
                """, (lote['id'], fecha_produccion, empresa_id)).fetchone()
                if existe:
                    continue
                # Crear alerta programada
                conn.execute("""
                    INSERT INTO alertas_programadas 
                    (tipo, lote_id, parte_id, fecha_alerta, fecha_produccion, mensaje)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, ('descongelado', lote['id'], parte['id'],
                      fecha_alerta.strftime('%Y-%m-%d %H:%M:%S'),
                      fecha_produccion,
                      f"Descongelar {lote['codigo_lote']} para producción del {fecha_produccion}"))
                conn.execute(
                    """
                    UPDATE alertas_programadas
                    SET empresa_id = ?,
                        cantidad_necesaria = ?,
                        unidad = ?
                    WHERE id = last_insert_rowid()
                    """,
                    (empresa_id, _to_float(ing['cantidad'] or 0, 0.0), ing['unidad'] or '')
                )

                # La fecha_descongelado real se graba al sacar el lote del congelador.
                conn.execute("""
                    UPDATE lotes
                    SET estado_descongelado = COALESCE(estado_descongelado, 'pendiente')
                    WHERE id = ?
                """, (lote['id'],))
                conn.execute("""
                    INSERT INTO movimientos_lotes (
                        lote_id, tipo, cantidad, destino_tipo, usuario, observaciones, documento, empresa_id
                    )
                    VALUES (?, 'alerta_descongelado', 0, 'appcc', ?, ?, ?, ?)
                """, (
                    lote['id'],
                    session.get('user_nombre') or 'admin',
                    f"Alerta APPCC generada para produccion del {fecha_produccion}",
                    f"Parte produccion {parte['id']}",
                    empresa_id,
                ))
                
                alertas_generadas += 1
    
    conn.commit()
    conn.close()
    
    return {'mensaje': f'{alertas_generadas} alertas generadas'}, 200

# ============================================
# HISTORIAL DE LOTE (TRAZABILIDAD)
# ============================================
@lotes_bp.route('/<int:lote_id>/historial')
@login_required
def historial_lote(lote_id):
    """Muestra el historial completo de un lote"""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    next_url = request.args.get('next') or url_for('lotes.lista_lotes')
    if '://' in next_url or next_url.startswith('//'):
        next_url = url_for('lotes.lista_lotes')
    _asegurar_lotes_schema(conn)
    conn.commit()
    
    # Datos del lote
    lote = conn.execute("""
        SELECT l.*, p.nombre as producto_nombre, p.unidad,
               pr.nombre as proveedor_nombre
        FROM lotes l
        JOIN productos p ON l.producto_id = p.id
        LEFT JOIN proveedores pr ON l.proveedor_id = pr.id
        WHERE l.id = ? AND l.empresa_id = ?
    """, (lote_id, empresa_id)).fetchone()
    
    if not lote:
        conn.close()
        flash(' Lote no encontrado', 'danger')
        return redirect(url_for('lotes.lista_lotes'))
    
    deterioros = conn.execute("""
        SELECT *
        FROM registros_deterioro_lotes
        WHERE lote_id = ?
        ORDER BY fecha DESC
    """, (lote_id,)).fetchall()

    # Movimientos del lote
    movimientos = conn.execute("""
        SELECT id, lote_id, fecha, tipo, cantidad,
               NULL AS ubicacion_origen,
               destino_tipo AS ubicacion_destino,
               NULL AS temperatura,
               usuario AS responsable,
               documento,
               observaciones
        FROM movimientos_lotes
        WHERE lote_id = ?
        ORDER BY fecha DESC
    """, (lote_id,)).fetchall()
    
    # Controles de descongelado
    controles = conn.execute("""
        SELECT * FROM registros_descongelado
        WHERE lote_id = ?
        ORDER BY fecha_control DESC
    """, (lote_id,)).fetchall()
    
    # Consumos en producción
    desinfecciones = conn.execute("""
        SELECT *
        FROM registros_desinfeccion
        WHERE lote_id = ?
        ORDER BY fecha DESC
    """, (lote_id,)).fetchall()

    consumos = conn.execute("""
        SELECT c.*, NULL AS usuario, pp.fecha as fecha_produccion, pp.turno,
               lp.receta_id, r.nombre as receta_nombre
        FROM consumo_produccion c
        JOIN lineas_produccion lp ON c.linea_produccion_id = lp.id
        JOIN partes_produccion pp ON lp.parte_id = pp.id
        JOIN recetas r ON lp.receta_id = r.id
        WHERE c.lote_id = ?
        ORDER BY pp.fecha DESC
    """, (lote_id,)).fetchall()
    
    conn.close()
    
    return render_template('lotes/historial_lote.html',
                         lote=lote,
                         movimientos=movimientos,
                         controles=controles,
                         desinfecciones=desinfecciones,
                         deterioros=deterioros,
                         consumos=consumos,
                         next_url=next_url)

# ============================================
# REGISTRAR MOVIMIENTO DE LOTE (API)
# ============================================
@lotes_bp.route('/api/registrar_movimiento_lote', methods=['POST'])
@login_required
def registrar_movimiento_lote():
    """API para registrar movimientos de un lote"""
    data = request.json
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_lotes_schema(conn)
    conn.commit()
    
    conn.execute("""
        INSERT INTO movimientos_lotes
        (lote_id, tipo, cantidad, destino_tipo, destino_id, usuario, observaciones, documento, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data['lote_id'],
        data['tipo'],
        data.get('cantidad'),
        data.get('destino_tipo') or data.get('ubicacion_destino'),
        data.get('destino_id') or data.get('parte_id'),
        session.get('user_nombre'),
        data.get('observaciones'),
        data.get('documento'),
        empresa_id
    ))
    
    conn.commit()
    conn.close()
    
    return {'status': 'ok'}

# ============================================
# TRAZABILIDAD DE PRODUCTO
# ============================================
@lotes_bp.route('/trazabilidad/producto/<int:producto_id>')
@login_required
def trazabilidad_producto(producto_id):
    """Muestra todos los lotes de un producto y su estado"""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_lotes_schema(conn)
    conn.commit()
    
    producto = conn.execute("SELECT * FROM productos WHERE id = ? AND empresa_id = ?", 
                           (producto_id, empresa_id)).fetchone()
    
    lotes = conn.execute("""
        SELECT l.*, 
               (SELECT COUNT(*) FROM consumo_produccion WHERE lote_id = l.id) as veces_consumido,
               (SELECT fecha FROM movimientos_lotes WHERE lote_id = l.id ORDER BY fecha DESC LIMIT 1) as ultimo_movimiento
        FROM lotes l
        WHERE l.producto_id = ? AND l.empresa_id = ?
        ORDER BY l.fecha_recepcion DESC
    """, (producto_id, empresa_id)).fetchall()
    
    conn.close()
    
    return render_template('lotes/trazabilidad_producto.html',
                         producto=producto,
                         lotes=lotes)

# ============================================
# TRAZABILIDAD DE PLATO (BSQUEDA)
# ============================================
@lotes_bp.route('/trazabilidad/plato')
@login_required
def trazabilidad_plato():
    """Buscador para ver qué lotes se usaron en un plato"""
    plato = request.args.get('plato')
    fecha = request.args.get('fecha')
    
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_lotes_schema(conn)
    conn.commit()
    
    query = """
        SELECT c.*, l.codigo_lote, p.nombre as producto_nombre,
               pp.fecha, pp.turno, r.nombre as receta_nombre
        FROM consumo_produccion c
        JOIN lotes l ON c.lote_id = l.id
        JOIN productos p ON l.producto_id = p.id
        JOIN lineas_produccion lp ON c.linea_produccion_id = lp.id
        JOIN partes_produccion pp ON lp.parte_id = pp.id
        JOIN recetas r ON lp.receta_id = r.id
        WHERE pp.empresa_id = ?
    """
    params = [empresa_id]
    
    if plato:
        query += " AND r.nombre LIKE ?"
        params.append(f'%{plato}%')
    
    if fecha:
        query += " AND date(pp.fecha) = ?"
        params.append(fecha)
    
    query += " ORDER BY pp.fecha DESC LIMIT 100"
    
    resultados = conn.execute(query, params).fetchall()
    conn.close()
    
    return render_template('lotes/trazabilidad_plato.html',
                         resultados=resultados,
                         plato_busqueda=plato,
                         fecha_busqueda=fecha)


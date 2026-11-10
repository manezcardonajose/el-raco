# blueprints/mantenimiento/routes.py
from flask import render_template, request, redirect, url_for, session, flash
from database import get_db
from datetime import datetime, timedelta
from . import mantenimiento_bp
from blueprints.auth.routes import login_required
from helpers.operativa_helper import ensure_equipos_unificados_schema, ensure_mantenimiento_context_schema, ensure_incidencias_context_schema
from helpers.centros_helper import asignar_centro_desde_departamento, centro_activo_id
import re
import unicodedata

# ============================================
# FUNCIONES AUXILIARES
# ============================================
def generar_numero_ot():
    """Genera un número de orden de trabajo tipo OT-20250307-001"""
    fecha = datetime.now().strftime("%Y%m%d")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total FROM ordenes_trabajo WHERE fecha_apertura LIKE ?", (f"{datetime.now().strftime('%Y-%m-%d')}%",))
    total_hoy = cursor.fetchone()['total'] + 1
    conn.close()
    return f"OT-{fecha}-{total_hoy:03d}"


def generar_numero_ot_conn(conn):
    fecha = datetime.now().strftime("%Y%m%d")
    hoy = datetime.now().strftime("%Y-%m-%d")
    total_hoy = conn.execute(
        "SELECT COUNT(*) as total FROM ordenes_trabajo WHERE fecha_apertura LIKE ?",
        (f"{hoy}%",),
    ).fetchone()['total']
    for extra in range(1, 1000):
        numero = f"OT-{fecha}-{total_hoy + extra:03d}"
        if not conn.execute("SELECT 1 FROM ordenes_trabajo WHERE numero_orden = ?", (numero,)).fetchone():
            return numero
    return f"OT-{fecha}-{int(datetime.now().timestamp())}"


def _ensure_mantenimiento_schema(conn):
    ensure_mantenimiento_context_schema(conn)
    ensure_incidencias_context_schema(conn)
    ensure_equipos_unificados_schema(conn)
    if _table_exists(conn, "equipos_mantenimiento"):
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(equipos_mantenimiento)")]
        if "centro_id" not in cols:
            conn.execute("ALTER TABLE equipos_mantenimiento ADD COLUMN centro_id INTEGER")


def _to_int_or_none(value):
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def _to_float_or_none(value):
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _tipo_almacen_desde_equipo(equipo):
    texto = f"{equipo['tipo'] or ''} {equipo['nombre'] or ''}".lower()
    if "congel" in texto:
        return "congelado"
    if any(token in texto for token in ("frio", "camara", "botellero", "vitrina", "nevera", "refriger")):
        return "botellero" if "botellero" in texto else "refrigerado"
    return "seco"


def _sincronizar_equipo_con_almacen(conn, equipo_id, empresa_id, crear_almacen=False, tipo_almacen=None, controla_temperatura=False):
    equipo = conn.execute("SELECT * FROM equipos_mantenimiento WHERE id = ? AND empresa_id = ?", (equipo_id, empresa_id)).fetchone()
    if not equipo or not crear_almacen:
        return
    tipo_almacen = tipo_almacen or _tipo_almacen_desde_equipo(equipo)
    controla_temperatura = bool(controla_temperatura or equipo["requiere_appcc"] or tipo_almacen in {"refrigerado", "congelado", "botellero", "camara", "vitrina"})
    almacen_id = equipo["almacen_id"]
    if not almacen_id:
        row = conn.execute(
            """
            SELECT id
            FROM almacenes
            WHERE empresa_id = ?
              AND centro_id = ?
              AND LOWER(nombre) = LOWER(?)
            LIMIT 1
            """,
            (empresa_id, equipo["centro_id"], equipo["nombre"]),
        ).fetchone()
        almacen_id = row["id"] if row else None
    if not almacen_id:
        conn.execute(
            """
            INSERT INTO almacenes
            (nombre, tipo, ubicacion, responsable, activo, empresa_id, departamento_id,
             subdepartamento_id, equipo_mantenimiento_id, requiere_control_temperatura,
             rango_min, rango_max, centro_id)
            VALUES (?, ?, ?, '', 1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                equipo["nombre"],
                tipo_almacen,
                equipo["ubicacion"],
                empresa_id,
                equipo["departamento_id"],
                equipo["subdepartamento_id"],
                equipo_id,
                1 if controla_temperatura else 0,
                -24 if tipo_almacen == "congelado" else (0 if controla_temperatura else None),
                -18 if tipo_almacen == "congelado" else (5 if controla_temperatura else None),
                equipo["centro_id"],
            ),
        )
        almacen_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    else:
        conn.execute(
            """
            UPDATE almacenes
            SET tipo = ?,
                ubicacion = ?,
                departamento_id = ?,
                subdepartamento_id = ?,
                equipo_mantenimiento_id = ?,
                requiere_control_temperatura = ?,
                centro_id = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (
                tipo_almacen,
                equipo["ubicacion"],
                equipo["departamento_id"],
                equipo["subdepartamento_id"],
                equipo_id,
                1 if controla_temperatura else 0,
                equipo["centro_id"],
                almacen_id,
                empresa_id,
            ),
        )
    conn.execute(
        """
        UPDATE equipos_mantenimiento
        SET almacen_id = ?,
            requiere_appcc = CASE WHEN ? = 1 THEN 1 ELSE requiere_appcc END
        WHERE id = ? AND empresa_id = ?
        """,
        (almacen_id, 1 if controla_temperatura else 0, equipo_id, empresa_id),
    )


def _to_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _slug_codigo(value):
    texto = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^A-Za-z0-9]+", "-", texto).strip("-").upper()
    return texto[:36] or "GENERAL"


def _generar_numero_pedido_mantenimiento(conn, empresa_id):
    fecha = datetime.now().strftime("%Y%m%d")
    total_hoy = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM pedidos
        WHERE empresa_id = ?
          AND numero_pedido LIKE ?
        """,
        (empresa_id, f"PED-MTO-{fecha}-%"),
    ).fetchone()["total"] or 0
    for extra in range(1, 1000):
        numero = f"PED-MTO-{fecha}-{total_hoy + extra:03d}"
        if not conn.execute("SELECT 1 FROM pedidos WHERE numero_pedido = ?", (numero,)).fetchone():
            return numero
    return f"PED-MTO-{fecha}-{int(datetime.now().timestamp())}"


def _departamento_mantenimiento_id(conn, empresa_id):
    row = conn.execute(
        """
        SELECT id
        FROM departamentos
        WHERE empresa_id = ?
          AND LOWER(nombre) LIKE '%mantenimiento%'
        ORDER BY id
        LIMIT 1
        """,
        (empresa_id,),
    ).fetchone()
    return row["id"] if row else None


def _producto_repuesto(conn, repuesto, proveedor_id, empresa_id):
    producto_id = repuesto["producto_id"] if "producto_id" in repuesto.keys() else None
    if producto_id:
        existente = conn.execute(
            "SELECT id FROM productos WHERE id = ? AND empresa_id = ?",
            (producto_id, empresa_id),
        ).fetchone()
        if existente:
            return producto_id

    codigo_base = f"MTO-{_slug_codigo(repuesto['codigo'] or repuesto['nombre'])}"
    existente = conn.execute(
        """
        SELECT id
        FROM productos
        WHERE empresa_id = ?
          AND (codigo = ? OR LOWER(nombre) = LOWER(?))
        ORDER BY id
        LIMIT 1
        """,
        (empresa_id, codigo_base, repuesto["nombre"]),
    ).fetchone()
    if existente:
        producto_id = existente["id"]
    else:
        cur = conn.execute(
            """
            INSERT INTO productos
            (nombre, cantidad, unidad, tipo_conservacion, familia, precio, stock_minimo,
             codigo, referencia_proveedor, ubicacion, observaciones, activo, empresa_id, proveedor_id)
            VALUES (?, 0, ?, 'seco', 'mantenimiento', ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                repuesto["nombre"],
                repuesto["unidad"] or "ud",
                _to_float(repuesto["precio_compra"], 0.0),
                _to_float(repuesto["stock_minimo"], 0.0),
                codigo_base,
                repuesto["referencia_proveedor"] if "referencia_proveedor" in repuesto.keys() else "",
                repuesto["ubicacion_almacen"] if "ubicacion_almacen" in repuesto.keys() else "",
                f"Producto creado desde repuesto de mantenimiento {repuesto['codigo'] or ''}".strip(),
                empresa_id,
                proveedor_id,
            ),
        )
        producto_id = cur.lastrowid

    conn.execute(
        "UPDATE repuestos SET producto_id = ? WHERE id = ? AND empresa_id = ?",
        (producto_id, repuesto["id"], empresa_id),
    )
    if proveedor_id and _table_exists(conn, "productos_proveedores"):
        relacion = conn.execute(
            """
            SELECT id
            FROM productos_proveedores
            WHERE producto_id = ? AND proveedor_id = ?
            LIMIT 1
            """,
            (producto_id, proveedor_id),
        ).fetchone()
        if relacion:
            conn.execute(
                """
                UPDATE productos_proveedores
                SET precio = ?, es_principal = 1, fecha_actualizacion = CURRENT_DATE
                WHERE id = ?
                """,
                (_to_float(repuesto["precio_compra"], 0.0), relacion["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO productos_proveedores
                (producto_id, proveedor_id, precio, es_principal)
                VALUES (?, ?, ?, 1)
                """,
                (producto_id, proveedor_id, _to_float(repuesto["precio_compra"], 0.0)),
            )
    return producto_id


def _codigo_repuesto_libre(conn, empresa_id, nombre, referencia=None):
    base = _slug_codigo(referencia or nombre)
    prefijo = f"MTO-{base}"
    codigo = prefijo[:48]
    for i in range(1, 1000):
        existente = conn.execute(
            """
            SELECT id
            FROM repuestos
            WHERE empresa_id = ?
              AND COALESCE(codigo, '') = ?
            LIMIT 1
            """,
            (empresa_id, codigo),
        ).fetchone()
        if not existente:
            return codigo
        sufijo = f"-{i:02d}"
        codigo = f"{prefijo[:48 - len(sufijo)]}{sufijo}"
    return f"MTO-{int(datetime.now().timestamp())}"


def _crear_repuesto_libre(conn, empresa_id, orden, nombre, proveedor_id, referencia, unidad, precio, observaciones, usuario):
    nombre = (nombre or "").strip()
    if not nombre:
        return None
    codigo = _codigo_repuesto_libre(conn, empresa_id, nombre, referencia)
    descripcion = "Pieza no catalogada creada desde una orden de mantenimiento."
    if observaciones:
        descripcion += f"\n{observaciones}"
    compatible = f"Orden {orden['numero_orden']} - equipo {orden['equipo_id'] or '-'}"
    cur = conn.execute(
        """
        INSERT INTO repuestos
        (codigo, nombre, descripcion, proveedor_id, referencia_proveedor,
         stock_actual, stock_minimo, precio_compra, unidad, ubicacion_almacen,
         compatible_con, activo, empresa_id)
        VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, 1, ?)
        """,
        (
            codigo,
            nombre,
            descripcion,
            proveedor_id,
            referencia or "",
            precio or 0,
            unidad or "ud",
            orden["ubicacion"] or "",
            compatible,
            empresa_id,
        ),
    )
    repuesto_id = cur.lastrowid
    conn.execute(
        """
        INSERT INTO historial_mantenimiento
        (equipo_id, fecha, tipo, orden_id, descripcion, tecnico, observaciones, empresa_id)
        VALUES (?, ?, 'alta_repuesto', ?, ?, ?, ?, ?)
        """,
        (
            orden["equipo_id"],
            datetime.now(),
            orden["id"],
            f"Repuesto provisional creado para compra: {codigo} - {nombre}",
            usuario,
            observaciones or None,
            empresa_id,
        ),
    )
    return conn.execute(
        "SELECT * FROM repuestos WHERE id = ? AND empresa_id = ?",
        (repuesto_id, empresa_id),
    ).fetchone()


def _contexto_equipo(conn, equipo_id, empresa_id):
    if not equipo_id:
        return None
    return conn.execute(
        """
        SELECT id, departamento_id, subdepartamento_id, punto_venta_id, ubicacion,
               habitacion_id, zona_id
        FROM equipos_mantenimiento
        WHERE id = ? AND empresa_id = ?
        """,
        (equipo_id, empresa_id),
    ).fetchone()


def _equipo_aviso_mantenimiento(conn, empresa_id, contexto):
    equipo = _contexto_equipo(conn, contexto.get("equipo_id"), empresa_id)
    if equipo:
        return equipo["id"], equipo
    ubicacion = contexto.get("ubicacion") or "Ubicacion sin definir"
    codigo = "AVISO-{dept}-{pv}-{ubi}".format(
        dept=contexto.get("departamento_id") or "GEN",
        pv=contexto.get("punto_venta_id") or contexto.get("subdepartamento_id") or "LOC",
        ubi=_slug_codigo(ubicacion),
    )
    existente = conn.execute(
        "SELECT * FROM equipos_mantenimiento WHERE empresa_id = ? AND COALESCE(codigo, '') = ? LIMIT 1",
        (empresa_id, codigo),
    ).fetchone()
    if existente:
        return existente["id"], existente
    cursor = conn.execute(
        """
        INSERT INTO equipos_mantenimiento
        (codigo, nombre, tipo, familia, categoria_operativa, clase_activo, criticidad,
         inventariable, requiere_appcc, ubicacion, departamento_id, subdepartamento_id,
         punto_venta_id, origen_modulo, activo, empresa_id)
        VALUES (?, ?, 'ubicacion', 'aviso_departamento', 'mantenimiento',
                'ubicacion', ?, 1, 0, ?, ?, ?, ?, 'aviso_departamento', 1, ?)
        """,
        (
            codigo,
            f"{ubicacion} - avisos departamento",
            contexto.get("prioridad") or "media",
            ubicacion,
            contexto.get("departamento_id"),
            contexto.get("subdepartamento_id"),
            contexto.get("punto_venta_id"),
            empresa_id,
        ),
    )
    equipo_id = cursor.lastrowid
    equipo = conn.execute("SELECT * FROM equipos_mantenimiento WHERE id = ?", (equipo_id,)).fetchone()
    return equipo_id, equipo


def _proxima_fecha_preventivo(tarea, fecha_base=None):
    fecha = fecha_base or datetime.now().date()
    intervalo = _to_int_or_none(tarea['intervalo_dias'] if 'intervalo_dias' in tarea.keys() else None)
    if intervalo:
        return fecha + timedelta(days=intervalo)
    frecuencia = (tarea['frecuencia'] if 'frecuencia' in tarea.keys() else 'mensual') or 'mensual'
    if frecuencia == 'diario':
        return fecha + timedelta(days=1)
    if frecuencia == 'semanal':
        return fecha + timedelta(weeks=1)
    if frecuencia == 'trimestral':
        return fecha + timedelta(days=90)
    if frecuencia == 'anual':
        return fecha + timedelta(days=365)
    return fecha + timedelta(days=30)


def _opciones_aviso_mantenimiento(conn, empresa_id):
    return {
        "departamentos": conn.execute(
            "SELECT id, nombre FROM departamentos WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
            (empresa_id,),
        ).fetchall(),
        "subdepartamentos": conn.execute(
            "SELECT id, nombre, departamento_id FROM subdepartamentos WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
            (empresa_id,),
        ).fetchall(),
        "puntos_venta": conn.execute(
            "SELECT id, nombre FROM puntos_venta WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
            (empresa_id,),
        ).fetchall(),
        "equipos": conn.execute(
            """
            SELECT id, codigo, nombre, ubicacion, departamento_id, punto_venta_id
            FROM equipos_mantenimiento
            WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
            ORDER BY nombre
            """,
            (empresa_id,),
        ).fetchall(),
    }

# ============================================
# DASHBOARD PRINCIPAL
# ============================================
@mantenimiento_bp.route('/')
@login_required
def dashboard():
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    
    # Estadísticas básicas
    total_equipos = conn.execute("SELECT COUNT(*) as total FROM equipos_mantenimiento WHERE activo = 1 AND empresa_id = ?", (empresa_id,)).fetchone()['total']
    
    # Órdenes abiertas
    ot_abiertas = conn.execute("SELECT COUNT(*) as total FROM ordenes_trabajo WHERE estado IN ('abierta', 'asignada', 'en_proceso') AND empresa_id = ?", (empresa_id,)).fetchone()['total']
    
    # Urgentes
    ot_urgentes = conn.execute("SELECT COUNT(*) as total FROM ordenes_trabajo WHERE prioridad = 'urgente' AND estado IN ('abierta', 'asignada', 'en_proceso') AND empresa_id = ?", (empresa_id,)).fetchone()['total']
    
    # Stock bajo en repuestos
    repuestos_bajos = conn.execute("""
        SELECT * FROM repuestos 
        WHERE stock_actual <= stock_minimo AND stock_actual > 0
        AND activo = 1 AND empresa_id = ?
        ORDER BY (stock_actual * 1.0 / stock_minimo) ASC
        LIMIT 10
    """, (empresa_id,)).fetchall()
    
    # Próximos mantenimientos (7 días)
    proximos = conn.execute("""
        SELECT mp.*, e.nombre as equipo_nombre
        FROM mantenimiento_preventivo mp
        JOIN equipos_mantenimiento e ON mp.equipo_id = e.id
        WHERE mp.proxima_ejecucion <= date('now', '+7 days')
        AND mp.proxima_ejecucion >= date('now')
        AND mp.activo = 1
        AND e.empresa_id = ?
        ORDER BY mp.proxima_ejecucion ASC
        LIMIT 10
    """, (empresa_id,)).fetchall()
    
    # Últimas 10 órdenes
    ultimas_ot = conn.execute("""
        SELECT ot.*, e.nombre as equipo_nombre
        FROM ordenes_trabajo ot
        JOIN equipos_mantenimiento e ON ot.equipo_id = e.id
        WHERE ot.empresa_id = ?
        ORDER BY ot.fecha_apertura DESC
        LIMIT 10
    """, (empresa_id,)).fetchall()
    
    # Datos para gráficos
    estados = conn.execute("""
        SELECT estado, COUNT(*) as total
        FROM ordenes_trabajo
        WHERE empresa_id = ?
        GROUP BY estado
    """, (empresa_id,)).fetchall()
    
    prioridades = conn.execute("""
        SELECT prioridad, COUNT(*) as total
        FROM ordenes_trabajo
        WHERE empresa_id = ?
        GROUP BY prioridad
    """, (empresa_id,)).fetchall()

    preventivos_vencidos = conn.execute("""
        SELECT COUNT(*) AS total
        FROM mantenimiento_preventivo mp
        JOIN equipos_mantenimiento e ON mp.equipo_id = e.id
        WHERE mp.activo = 1
          AND e.empresa_id = ?
          AND mp.proxima_ejecucion IS NOT NULL
          AND date(mp.proxima_ejecucion) < date('now')
    """, (empresa_id,)).fetchone()['total']

    equipos_criticos = conn.execute("""
        SELECT e.*,
               (SELECT COUNT(*) FROM ordenes_trabajo ot
                WHERE ot.equipo_id = e.id
                  AND ot.empresa_id = e.empresa_id
                  AND ot.estado IN ('abierta', 'asignada', 'en_proceso')) AS ot_abiertas
        FROM equipos_mantenimiento e
        WHERE e.empresa_id = ?
          AND COALESCE(e.activo, 1) = 1
          AND LOWER(COALESCE(e.criticidad, 'media')) IN ('alta', 'critica', 'urgente')
        ORDER BY ot_abiertas DESC, e.nombre
        LIMIT 8
    """, (empresa_id,)).fetchall()

    carga_departamentos = conn.execute("""
        SELECT COALESCE(d.nombre, 'Sin departamento') AS departamento,
               COUNT(*) AS total,
               SUM(CASE WHEN ot.prioridad = 'urgente' THEN 1 ELSE 0 END) AS urgentes,
               SUM(CASE WHEN ot.estado = 'en_proceso' THEN 1 ELSE 0 END) AS en_proceso
        FROM ordenes_trabajo ot
        LEFT JOIN departamentos d ON d.id = ot.departamento_id
        WHERE ot.empresa_id = ?
          AND ot.estado IN ('abierta', 'asignada', 'en_proceso')
        GROUP BY COALESCE(d.nombre, 'Sin departamento')
        ORDER BY urgentes DESC, total DESC
        LIMIT 8
    """, (empresa_id,)).fetchall()

    coste_mes = conn.execute("""
        SELECT COALESCE(SUM(COALESCE(coste_real, coste_estimado, 0)), 0) AS total
        FROM ordenes_trabajo
        WHERE empresa_id = ?
          AND date(COALESCE(fecha_cierre, fecha_fin, fecha_apertura)) >= date('now', 'start of month')
    """, (empresa_id,)).fetchone()['total']
    
    conn.close()
    
    return render_template('mantenimiento/dashboard.html',
                         total_equipos=total_equipos,
                         ot_abiertas=ot_abiertas,
                         ot_urgentes=ot_urgentes,
                         repuestos_bajos=repuestos_bajos,
                         proximos=proximos,
                         ultimas_ot=ultimas_ot,
                         estados=estados,
                         prioridades=prioridades,
                         preventivos_vencidos=preventivos_vencidos,
                         equipos_criticos=equipos_criticos,
                         carga_departamentos=carga_departamentos,
                         coste_mes=coste_mes)


@mantenimiento_bp.route('/aviso', methods=['GET', 'POST'])
@login_required
def aviso_mantenimiento():
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    creado = request.args.get('creado') or ''
    orden_id = request.args.get('orden_id') or ''

    if request.method == 'POST':
        descripcion = (request.form.get('descripcion') or '').strip()
        if not descripcion:
            flash('Describe el problema para poder crear el aviso.', 'warning')
            return redirect(url_for('mantenimiento.aviso_mantenimiento'))

        contexto = {
            "equipo_id": _to_int_or_none(request.form.get('equipo_id')),
            "departamento_id": _to_int_or_none(request.form.get('departamento_id')) or session.get('user_departamento_id'),
            "subdepartamento_id": _to_int_or_none(request.form.get('subdepartamento_id')) or session.get('user_subdepartamento_id'),
            "punto_venta_id": _to_int_or_none(request.form.get('punto_venta_id')),
            "ubicacion": (request.form.get('ubicacion') or '').strip(),
            "prioridad": request.form.get('prioridad') or 'media',
            "tipo": request.form.get('tipo') or 'correctivo',
        }
        equipo_id, equipo = _equipo_aviso_mantenimiento(conn, empresa_id, contexto)
        contexto["departamento_id"] = contexto["departamento_id"] or (equipo["departamento_id"] if equipo else None)
        contexto["subdepartamento_id"] = contexto["subdepartamento_id"] or (equipo["subdepartamento_id"] if equipo else None)
        contexto["punto_venta_id"] = contexto["punto_venta_id"] or (equipo["punto_venta_id"] if equipo else None)
        contexto["ubicacion"] = contexto["ubicacion"] or (equipo["ubicacion"] if equipo else "") or "Ubicacion sin definir"
        habitacion_id = equipo["habitacion_id"] if equipo and "habitacion_id" in equipo.keys() else None
        zona_id = equipo["zona_id"] if equipo and "zona_id" in equipo.keys() else None

        usuario = session.get('user_nombre') or session.get('user_email') or 'usuario'
        incidencia_cur = conn.execute(
            """
            INSERT INTO incidencias
            (tipo, usuario, descripcion, gravedad, estado, observaciones,
             origen_modulo, destino_modulo, categoria_operativa, departamento_id,
             subdepartamento_id, punto_venta_id, ubicacion, habitacion_id, zona_id,
             equipo_mantenimiento_id, requiere_mantenimiento, requiere_appcc,
             origen_tabla, empresa_id)
            VALUES ('mantenimiento', ?, ?, ?, 'abierta', ?,
                    'aviso_departamento', 'mantenimiento', ?, ?, ?, ?, ?, ?, ?,
                    ?, 1, 0, 'aviso_mantenimiento', ?)
            """,
            (
                usuario,
                descripcion,
                contexto["prioridad"],
                f"Aviso creado por {usuario}",
                contexto["tipo"],
                contexto["departamento_id"],
                contexto["subdepartamento_id"],
                contexto["punto_venta_id"],
                contexto["ubicacion"],
                habitacion_id,
                zona_id,
                equipo_id,
                empresa_id,
            ),
        )
        incidencia_id = incidencia_cur.lastrowid
        numero_orden = generar_numero_ot_conn(conn)
        descripcion_ot = (
            f"Aviso de mantenimiento desde departamento.\n"
            f"Usuario: {usuario}\n"
            f"Ubicacion: {contexto['ubicacion']}\n"
            f"Detalle: {descripcion}\n"
            f"Incidencia #{incidencia_id}"
        )
        orden_cur = conn.execute(
            """
            INSERT INTO ordenes_trabajo
            (numero_orden, equipo_id, tipo, prioridad, estado, descripcion, tecnico_asignado,
             incidencia_id, observaciones, origen_modulo, origen_tipo, origen_id,
             departamento_id, subdepartamento_id, punto_venta_id, ubicacion, habitacion_id,
             zona_id, requiere_appcc, empresa_id)
            VALUES (?, ?, ?, ?, 'abierta', ?, '', ?, 'Aviso creado por departamento',
                    'aviso_departamento', 'aviso_mantenimiento', ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                numero_orden,
                equipo_id,
                contexto["tipo"],
                contexto["prioridad"],
                descripcion_ot,
                incidencia_id,
                incidencia_id,
                contexto["departamento_id"],
                contexto["subdepartamento_id"],
                contexto["punto_venta_id"],
                contexto["ubicacion"],
                habitacion_id,
                zona_id,
                empresa_id,
            ),
        )
        orden_id_new = orden_cur.lastrowid
        conn.execute(
            """
            UPDATE incidencias
            SET referencia_id = ?, origen_id = ?, orden_trabajo_id = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (orden_id_new, orden_id_new, orden_id_new, incidencia_id, empresa_id),
        )
        conn.commit()
        conn.close()
        flash(f'Aviso enviado a mantenimiento. Orden {numero_orden}.', 'success')
        return redirect(url_for('mantenimiento.aviso_mantenimiento', creado=numero_orden, orden_id=orden_id_new))

    opciones = _opciones_aviso_mantenimiento(conn, empresa_id)
    conn.close()
    return render_template(
        'mantenimiento/aviso_mantenimiento.html',
        creado=creado,
        orden_id=orden_id,
        departamento_actual=session.get('user_departamento_id'),
        subdepartamento_actual=session.get('user_subdepartamento_id'),
        **opciones,
    )

# ============================================
# EQUIPOS
# ============================================
@mantenimiento_bp.route('/equipos')
@login_required
def lista_equipos():
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    centro_sql = " AND e.centro_id = ?" if centro_id else ""
    centro_params = [centro_id] if centro_id else []
    
    equipos = conn.execute("""
        SELECT e.*, p.nombre as proveedor_nombre,
               d.nombre as departamento_nombre,
               s.nombre as subdepartamento_nombre,
               pv.nombre as punto_venta_nombre,
               ea.nombre as equipo_appcc_nombre,
               ea.rango_min as appcc_rango_min,
               ea.rango_max as appcc_rango_max,
               a.nombre as almacen_nombre,
               a.tipo as almacen_tipo,
               (SELECT COUNT(*) FROM ordenes_trabajo WHERE equipo_id = e.id AND estado IN ('abierta', 'en_proceso')) as ot_pendientes
        FROM equipos_mantenimiento e
        LEFT JOIN proveedores p ON e.proveedor_id = p.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN subdepartamentos s ON e.subdepartamento_id = s.id
        LEFT JOIN puntos_venta pv ON e.punto_venta_id = pv.id
        LEFT JOIN equipos ea ON ea.id = e.equipo_appcc_id
        LEFT JOIN almacenes a ON a.id = e.almacen_id
        WHERE e.activo = 1 AND e.empresa_id = ?
    """ + centro_sql + """
        ORDER BY e.nombre
    """, [empresa_id] + centro_params).fetchall()
    
    conn.close()
    return render_template('mantenimiento/equipos.html', equipos=equipos)

@mantenimiento_bp.route('/equipos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_equipo():
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    
    if request.method == 'POST':
        nombre = request.form['nombre']
        tipo = request.form['tipo']
        codigo = request.form.get('codigo', '')
        familia = request.form.get('familia', '')
        clase_activo = request.form.get('clase_activo', '')
        criticidad = request.form.get('criticidad', 'media')
        inventariable = 1 if request.form.get('inventariable') else 0
        requiere_appcc = 1 if request.form.get('requiere_appcc') else 0
        marca = request.form.get('marca', '')
        modelo = request.form.get('modelo', '')
        numero_serie = request.form.get('numero_serie', '')
        fecha_compra = request.form.get('fecha_compra')
        fecha_instalacion = request.form.get('fecha_instalacion')
        garantia_meses = request.form.get('garantia_meses')
        proveedor_id = request.form.get('proveedor_id')
        ubicacion = request.form.get('ubicacion', '')
        departamento_id = request.form.get('departamento_id')
        subdepartamento_id = request.form.get('subdepartamento_id')
        punto_venta_id = request.form.get('punto_venta_id')
        centro_id = asignar_centro_desde_departamento(conn, departamento_id)
        
        conn.execute("""
            INSERT INTO equipos_mantenimiento 
            (codigo, nombre, tipo, familia, categoria_operativa, clase_activo, criticidad,
             inventariable, requiere_appcc, marca, modelo, numero_serie, fecha_compra, fecha_instalacion, 
             garantia_meses, proveedor_id, ubicacion, departamento_id, subdepartamento_id, 
             punto_venta_id, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, 'mantenimiento', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (codigo, nombre, tipo, familia, clase_activo, criticidad, inventariable, requiere_appcc,
              marca, modelo, numero_serie, fecha_compra, fecha_instalacion,
              garantia_meses, proveedor_id, ubicacion, departamento_id, subdepartamento_id,
              punto_venta_id, empresa_id, centro_id))
        _ensure_mantenimiento_schema(conn)
        conn.commit()
        conn.close()
        
        flash('✅ Equipo registrado correctamente', 'success')
        return redirect(url_for('mantenimiento.lista_equipos'))
    
    proveedores = conn.execute("SELECT id, nombre FROM proveedores WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    departamentos = conn.execute("SELECT id, nombre FROM departamentos WHERE activo = 1 ORDER BY nombre").fetchall()
    subdepartamentos = conn.execute("SELECT id, nombre FROM subdepartamentos WHERE activo = 1 ORDER BY nombre").fetchall()
    puntos_venta = conn.execute("SELECT id, nombre FROM puntos_venta WHERE activo = 1 ORDER BY nombre").fetchall()
    
    conn.close()
    return render_template('mantenimiento/nuevo_equipo.html',
                         proveedores=proveedores,
                         departamentos=departamentos,
                         subdepartamentos=subdepartamentos,
                         puntos_venta=puntos_venta)

@mantenimiento_bp.route('/equipos/<int:equipo_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_equipo(equipo_id):
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    equipo = conn.execute("SELECT * FROM equipos_mantenimiento WHERE id = ? AND empresa_id = ?", (equipo_id, empresa_id)).fetchone()
    if not equipo:
        conn.close()
        flash('Equipo no encontrado', 'danger')
        return redirect(url_for('mantenimiento.lista_equipos'))

    if request.method == 'POST':
        nombre = request.form['nombre']
        tipo = request.form['tipo']
        codigo = request.form.get('codigo', '')
        familia = request.form.get('familia', '')
        clase_activo = request.form.get('clase_activo', '')
        criticidad = request.form.get('criticidad', 'media')
        inventariable = 1 if request.form.get('inventariable') else 0
        requiere_appcc = 1 if request.form.get('requiere_appcc') else 0
        marca = request.form.get('marca', '')
        modelo = request.form.get('modelo', '')
        numero_serie = request.form.get('numero_serie', '')
        fecha_compra = request.form.get('fecha_compra')
        fecha_instalacion = request.form.get('fecha_instalacion')
        garantia_meses = request.form.get('garantia_meses')
        proveedor_id = request.form.get('proveedor_id') or None
        ubicacion = request.form.get('ubicacion', '')
        departamento_id = request.form.get('departamento_id') or None
        subdepartamento_id = request.form.get('subdepartamento_id') or None
        punto_venta_id = request.form.get('punto_venta_id') or None
        centro_id = asignar_centro_desde_departamento(conn, departamento_id) or equipo['centro_id'] or centro_activo_id()

        conn.execute("""
            UPDATE equipos_mantenimiento
            SET codigo = ?, nombre = ?, tipo = ?, familia = ?, clase_activo = ?,
                criticidad = ?, inventariable = ?, requiere_appcc = ?, marca = ?,
                modelo = ?, numero_serie = ?, fecha_compra = ?, fecha_instalacion = ?,
                garantia_meses = ?, proveedor_id = ?, ubicacion = ?, departamento_id = ?,
                subdepartamento_id = ?, punto_venta_id = ?, centro_id = ?
            WHERE id = ? AND empresa_id = ?
        """, (
            codigo, nombre, tipo, familia, clase_activo, criticidad, inventariable, requiere_appcc,
            marca, modelo, numero_serie, fecha_compra, fecha_instalacion, garantia_meses,
            proveedor_id, ubicacion, departamento_id, subdepartamento_id, punto_venta_id,
            centro_id, equipo_id, empresa_id,
        ))
        _sincronizar_equipo_con_almacen(
            conn,
            equipo_id,
            empresa_id,
            crear_almacen=bool(request.form.get('es_almacen')),
            tipo_almacen=request.form.get('tipo_almacen') or None,
            controla_temperatura=bool(request.form.get('controla_temperatura') or requiere_appcc),
        )
        conn.commit()
        conn.close()
        flash('Equipo actualizado correctamente', 'success')
        return redirect(url_for('mantenimiento.ver_equipo', equipo_id=equipo_id))

    proveedores = conn.execute("SELECT id, nombre FROM proveedores WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    centro = centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro else ""
    centro_params = [centro] if centro else []
    departamentos = conn.execute("SELECT id, nombre FROM departamentos WHERE activo = 1 AND empresa_id = ?" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params).fetchall()
    subdepartamentos = conn.execute("SELECT id, nombre, departamento_id FROM subdepartamentos WHERE activo = 1 AND empresa_id = ?" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params).fetchall()
    puntos_venta = conn.execute("SELECT id, nombre FROM puntos_venta WHERE activo = 1 AND empresa_id = ?" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params).fetchall()
    almacenes = conn.execute("SELECT id, nombre FROM almacenes WHERE activo = 1 AND empresa_id = ?" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params).fetchall()
    conn.close()
    return render_template(
        'mantenimiento/editar_equipo.html',
        equipo=equipo,
        proveedores=proveedores,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        puntos_venta=puntos_venta,
        almacenes=almacenes,
    )

@mantenimiento_bp.route('/equipos/<int:equipo_id>')
@login_required
def ver_equipo(equipo_id):
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    
    equipo = conn.execute("""
        SELECT e.*, p.nombre as proveedor_nombre,
               d.nombre as departamento_nombre,
               s.nombre as subdepartamento_nombre,
               pv.nombre as punto_venta_nombre,
               ea.nombre as equipo_appcc_nombre,
               ea.rango_min as appcc_rango_min,
               ea.rango_max as appcc_rango_max,
               a.nombre as almacen_nombre,
               a.tipo as almacen_tipo
        FROM equipos_mantenimiento e
        LEFT JOIN proveedores p ON e.proveedor_id = p.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN subdepartamentos s ON e.subdepartamento_id = s.id
        LEFT JOIN puntos_venta pv ON e.punto_venta_id = pv.id
        LEFT JOIN equipos ea ON ea.id = e.equipo_appcc_id
        LEFT JOIN almacenes a ON a.id = e.almacen_id
        WHERE e.id = ? AND e.empresa_id = ?
    """, (equipo_id, empresa_id)).fetchone()
    
    if not equipo:
        flash('Equipo no encontrado', 'danger')
        return redirect(url_for('mantenimiento.lista_equipos'))
    
    preventivos = conn.execute("""
        SELECT * FROM mantenimiento_preventivo
        WHERE equipo_id = ? AND activo = 1
        ORDER BY proxima_ejecucion
    """, (equipo_id,)).fetchall()
    
    historial = conn.execute("""
        SELECT * FROM ordenes_trabajo
        WHERE equipo_id = ?
        ORDER BY fecha_apertura DESC
        LIMIT 20
    """, (equipo_id,)).fetchall()
    repuestos_usados = conn.execute("""
        SELECT orp.*, r.nombre as repuesto_nombre, r.codigo, ot.numero_orden, ot.id as orden_id
        FROM ordenes_repuestos orp
        JOIN repuestos r ON orp.repuesto_id = r.id
        JOIN ordenes_trabajo ot ON orp.orden_id = ot.id
        WHERE ot.equipo_id = ?
        ORDER BY orp.id DESC
        LIMIT 50
    """, (equipo_id,)).fetchall()
    coste_total_ordenes = sum(float(row['coste_real'] or row['coste_estimado'] or 0) for row in historial)
    coste_total_repuestos = sum(float(row['cantidad'] or 0) * float(row['precio_unitario'] or 0) for row in repuestos_usados)
    total_horas = sum(float(row['horas_reales'] or row['horas_estimadas'] or 0) for row in historial)
    
    conn.close()
    
    return render_template('mantenimiento/ver_equipo.html',
                         equipo=equipo,
                         preventivos=preventivos,
                         historial=historial,
                         repuestos_usados=repuestos_usados,
                         coste_total_ordenes=coste_total_ordenes,
                         coste_total_repuestos=coste_total_repuestos,
                         total_horas=total_horas)

# ============================================
# ÓRDENES DE TRABAJO
# ============================================
@mantenimiento_bp.route('/ordenes')
@login_required
def lista_ordenes():
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    
    estado = request.args.get('estado', '')
    prioridad = request.args.get('prioridad', '')
    
    query = """
        SELECT ot.*, e.nombre as equipo_nombre, d.nombre AS departamento_nombre,
               s.nombre AS subdepartamento_nombre, pv.nombre AS punto_venta_nombre
        FROM ordenes_trabajo ot
        JOIN equipos_mantenimiento e ON ot.equipo_id = e.id
        LEFT JOIN departamentos d ON ot.departamento_id = d.id
        LEFT JOIN subdepartamentos s ON ot.subdepartamento_id = s.id
        LEFT JOIN puntos_venta pv ON ot.punto_venta_id = pv.id
        WHERE ot.empresa_id = ?
    """
    params = [empresa_id]
    
    if estado:
        query += " AND ot.estado = ?"
        params.append(estado)
    
    if prioridad:
        query += " AND ot.prioridad = ?"
        params.append(prioridad)
    
    query += " ORDER BY ot.fecha_apertura DESC"
    
    ordenes = conn.execute(query, params).fetchall()
    conn.close()
    
    return render_template('mantenimiento/ordenes.html', ordenes=ordenes)

@mantenimiento_bp.route('/ordenes/nueva', methods=['GET', 'POST'])
@login_required
def nueva_orden():
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    
    if request.method == 'POST':
        equipo_id = request.form['equipo_id']
        tipo = request.form['tipo']
        prioridad = request.form['prioridad']
        descripcion = request.form['descripcion']
        tecnico_asignado = request.form.get('tecnico_asignado', '')
        horas_estimadas = request.form.get('horas_estimadas')
        
        numero_orden = generar_numero_ot()
        
        equipo = conn.execute(
            "SELECT departamento_id, subdepartamento_id, punto_venta_id, ubicacion, habitacion_id, zona_id FROM equipos_mantenimiento WHERE id = ? AND empresa_id = ?",
            (equipo_id, empresa_id),
        ).fetchone()
        conn.execute("""
            INSERT INTO ordenes_trabajo 
            (numero_orden, equipo_id, tipo, prioridad, estado, descripcion, tecnico_asignado,
             horas_estimadas, origen_modulo, departamento_id, subdepartamento_id, punto_venta_id,
             ubicacion, habitacion_id, zona_id, empresa_id)
            VALUES (?, ?, ?, ?, 'abierta', ?, ?, ?, 'mantenimiento', ?, ?, ?, ?, ?, ?, ?)
        """, (
            numero_orden, equipo_id, tipo, prioridad, descripcion, tecnico_asignado, horas_estimadas,
            equipo['departamento_id'] if equipo else None,
            equipo['subdepartamento_id'] if equipo else None,
            equipo['punto_venta_id'] if equipo else None,
            equipo['ubicacion'] if equipo else None,
            equipo['habitacion_id'] if equipo else None,
            equipo['zona_id'] if equipo else None,
            empresa_id,
        ))
        
        conn.commit()
        conn.close()
        
        flash(f'✅ Orden {numero_orden} creada correctamente', 'success')
        return redirect(url_for('mantenimiento.lista_ordenes'))
    
    equipos = conn.execute("SELECT id, nombre FROM equipos_mantenimiento WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    conn.close()
    
    return render_template('mantenimiento/nueva_orden.html', equipos=equipos)

@mantenimiento_bp.route('/ordenes/<int:orden_id>')
@login_required
def ver_orden(orden_id):
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    
    orden = conn.execute("""
        SELECT ot.*, e.nombre as equipo_nombre, e.modelo, e.marca,
               d.nombre AS departamento_nombre, s.nombre AS subdepartamento_nombre,
               pv.nombre AS punto_venta_nombre, i.tipo AS incidencia_tipo,
               i.descripcion AS incidencia_descripcion
        FROM ordenes_trabajo ot
        JOIN equipos_mantenimiento e ON ot.equipo_id = e.id
        LEFT JOIN departamentos d ON ot.departamento_id = d.id
        LEFT JOIN subdepartamentos s ON ot.subdepartamento_id = s.id
        LEFT JOIN puntos_venta pv ON ot.punto_venta_id = pv.id
        LEFT JOIN incidencias i ON ot.incidencia_id = i.id
        WHERE ot.id = ? AND ot.empresa_id = ?
    """, (orden_id, empresa_id)).fetchone()
    
    if not orden:
        flash('Orden no encontrada', 'danger')
        return redirect(url_for('mantenimiento.lista_ordenes'))
    
    repuestos = conn.execute("""
        SELECT orp.*, r.nombre as repuesto_nombre, r.codigo
        FROM ordenes_repuestos orp
        JOIN repuestos r ON orp.repuesto_id = r.id
        WHERE orp.orden_id = ?
          AND COALESCE(orp.empresa_id, ?) = ?
        ORDER BY orp.id DESC
    """, (orden_id, empresa_id, empresa_id)).fetchall()

    repuestos_disponibles = conn.execute("""
        SELECT id, codigo, nombre, stock_actual, stock_minimo, precio_compra, unidad, ubicacion_almacen
        FROM repuestos
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
        ORDER BY nombre
    """, (empresa_id,)).fetchall()

    proveedores = conn.execute("""
        SELECT id, nombre
        FROM proveedores
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
        ORDER BY nombre
    """, (empresa_id,)).fetchall() if _table_exists(conn, "proveedores") else []

    solicitudes_compra = conn.execute("""
        SELECT mpc.*, p.numero_pedido, p.estado AS pedido_estado,
               r.codigo AS repuesto_codigo, r.nombre AS repuesto_nombre,
               pr.nombre AS proveedor_nombre
        FROM mantenimiento_pedidos_compra mpc
        LEFT JOIN pedidos p ON p.id = mpc.pedido_id
        LEFT JOIN repuestos r ON r.id = mpc.repuesto_id
        LEFT JOIN proveedores pr ON pr.id = mpc.proveedor_id
        WHERE mpc.orden_id = ? AND mpc.empresa_id = ?
        ORDER BY mpc.id DESC
    """, (orden_id, empresa_id)).fetchall()

    historial = conn.execute("""
        SELECT *
        FROM historial_mantenimiento
        WHERE orden_id = ?
        ORDER BY fecha DESC, id DESC
    """, (orden_id,)).fetchall()
    
    conn.close()
    
    return render_template(
        'mantenimiento/ver_orden.html',
        orden=orden,
        repuestos=repuestos,
        repuestos_disponibles=repuestos_disponibles,
        proveedores=proveedores,
        solicitudes_compra=solicitudes_compra,
        historial=historial,
    )


@mantenimiento_bp.route('/ordenes/<int:orden_id>/repuestos/agregar', methods=['POST'])
@login_required
def agregar_repuesto_orden(orden_id):
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    usuario = session.get('user_nombre') or session.get('user_email') or 'admin'

    orden = conn.execute(
        """
        SELECT *
        FROM ordenes_trabajo
        WHERE id = ? AND empresa_id = ?
        """,
        (orden_id, empresa_id),
    ).fetchone()
    if not orden:
        conn.close()
        flash('Orden no encontrada.', 'danger')
        return redirect(url_for('mantenimiento.lista_ordenes'))

    if orden['estado'] in ('completada', 'cancelada'):
        conn.close()
        flash('No se pueden anadir repuestos a una orden cerrada.', 'warning')
        return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))

    repuesto_id = _to_int_or_none(request.form.get('repuesto_id'))
    cantidad = _to_float(request.form.get('cantidad'), 0.0)
    observaciones = (request.form.get('observaciones') or '').strip()
    if not repuesto_id or cantidad <= 0:
        conn.close()
        flash('Selecciona repuesto y cantidad mayor que cero.', 'warning')
        return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))

    repuesto = conn.execute(
        """
        SELECT *
        FROM repuestos
        WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1
        """,
        (repuesto_id, empresa_id),
    ).fetchone()
    if not repuesto:
        conn.close()
        flash('Repuesto no encontrado.', 'warning')
        return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))

    stock_actual = _to_float(repuesto['stock_actual'], 0.0)
    precio = _to_float(request.form.get('precio_unitario'), _to_float(repuesto['precio_compra'], 0.0))
    if cantidad > stock_actual:
        conn.close()
        flash(f"No hay stock suficiente. Disponible: {stock_actual:.2f} {repuesto['unidad'] or 'ud'}.", 'warning')
        return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))

    conn.execute(
        """
        INSERT INTO ordenes_repuestos
        (orden_id, repuesto_id, cantidad, precio_unitario, observaciones, empresa_id, fecha_uso, usuario)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (orden_id, repuesto_id, cantidad, precio, observaciones or None, empresa_id, datetime.now(), usuario),
    )
    conn.execute(
        """
        UPDATE repuestos
        SET stock_actual = COALESCE(stock_actual, 0) - ?
        WHERE id = ? AND empresa_id = ?
        """,
        (cantidad, repuesto_id, empresa_id),
    )
    total_repuesto = round(cantidad * precio, 2)
    coste_anterior = _to_float(orden['coste_real'], 0.0)
    conn.execute(
        """
        UPDATE ordenes_trabajo
        SET coste_real = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (round(coste_anterior + total_repuesto, 2), orden_id, empresa_id),
    )
    conn.execute(
        """
        INSERT INTO historial_mantenimiento
        (equipo_id, fecha, tipo, orden_id, descripcion, tecnico, coste, observaciones, empresa_id)
        VALUES (?, ?, 'repuesto', ?, ?, ?, ?, ?, ?)
        """,
        (
            orden['equipo_id'],
            datetime.now(),
            orden_id,
            f"Repuesto usado: {repuesto['codigo']} - {repuesto['nombre']} ({cantidad:g} {repuesto['unidad'] or 'ud'})",
            usuario,
            total_repuesto,
            observaciones or None,
            empresa_id,
        ),
    )
    conn.commit()
    conn.close()
    flash('Repuesto anadido a la orden y stock descontado.', 'success')
    return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))


@mantenimiento_bp.route('/ordenes/<int:orden_id>/compras/solicitar', methods=['POST'])
@login_required
def solicitar_compra_repuesto_orden(orden_id):
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    try:
        from blueprints.compras.routes import _asegurar_estructura_compras

        _asegurar_estructura_compras(conn.cursor())
    except Exception:
        pass
    empresa_id = session.get('user_empresa_id', 1)
    usuario = session.get('user_nombre') or session.get('user_email') or 'admin'

    orden = conn.execute(
        """
        SELECT *
        FROM ordenes_trabajo
        WHERE id = ? AND empresa_id = ?
        """,
        (orden_id, empresa_id),
    ).fetchone()
    if not orden:
        conn.close()
        flash('Orden no encontrada.', 'danger')
        return redirect(url_for('mantenimiento.lista_ordenes'))
    if orden['estado'] in ('completada', 'cancelada'):
        conn.close()
        flash('No se pueden solicitar compras desde una orden cerrada.', 'warning')
        return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))

    repuesto_id = _to_int_or_none(request.form.get('repuesto_compra_id'))
    pieza_libre = (request.form.get('pieza_compra_nombre') or '').strip()
    referencia_libre = (request.form.get('referencia_pieza_compra') or '').strip()
    unidad_libre = (request.form.get('unidad_pieza_compra') or 'ud').strip() or 'ud'
    precio_estimado = _to_float_or_none(request.form.get('precio_compra_estimado'))
    cantidad = _to_float(request.form.get('cantidad_compra'), 0.0)
    proveedor_id = _to_int_or_none(request.form.get('proveedor_compra_id'))
    observaciones = (request.form.get('observaciones_compra') or '').strip()
    if not (repuesto_id or pieza_libre) or cantidad <= 0:
        conn.close()
        flash('Selecciona un repuesto o escribe una pieza no catalogada, con cantidad mayor que cero.', 'warning')
        return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))

    if pieza_libre:
        if not proveedor_id:
            conn.close()
            flash('Para pedir una pieza no catalogada selecciona proveedor. Si no se sabe, crea un proveedor provisional desde proveedores.', 'warning')
            return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))
        repuesto = _crear_repuesto_libre(
            conn,
            empresa_id,
            orden,
            pieza_libre,
            proveedor_id,
            referencia_libre,
            unidad_libre,
            precio_estimado or 0,
            observaciones,
            usuario,
        )
    else:
        repuesto = conn.execute(
            """
            SELECT *
            FROM repuestos
            WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1
            """,
            (repuesto_id, empresa_id),
        ).fetchone()
        if not repuesto:
            conn.close()
            flash('Repuesto no encontrado.', 'warning')
            return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))
        proveedor_id = proveedor_id or (repuesto['proveedor_id'] if 'proveedor_id' in repuesto.keys() else None)

    if not proveedor_id:
        conn.close()
        flash('El repuesto no tiene proveedor. Selecciona un proveedor para crear el pedido.', 'warning')
        return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))

    producto_id = _producto_repuesto(conn, repuesto, proveedor_id, empresa_id)
    numero_pedido = _generar_numero_pedido_mantenimiento(conn, empresa_id)
    departamento_id = orden['departamento_id'] or session.get('user_departamento_id') or _departamento_mantenimiento_id(conn, empresa_id)
    precio = precio_estimado if precio_estimado is not None else _to_float(repuesto['precio_compra'], 0.0)
    texto_obs = (
        f"Solicitud generada desde mantenimiento {orden['numero_orden']}.\n"
        f"Repuesto: {repuesto['codigo']} - {repuesto['nombre']}.\n"
        f"Ubicacion/equipo: {orden['ubicacion'] or ''} / equipo {orden['equipo_id']}."
    )
    if pieza_libre:
        texto_obs += "\nPieza creada como repuesto provisional no catalogado."
    if observaciones:
        texto_obs += f"\nObservaciones: {observaciones}"
    cur = conn.execute(
        """
        INSERT INTO pedidos (
            numero_pedido, proveedor_id, departamento_id, departamento_solicitante_id,
            subdepartamento_solicitante_id, punto_venta_id, fecha_entrega, observaciones,
            creado_por, solicitado_por, fecha_solicitud_autorizacion, estado, tipo_pedido, empresa_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), 'pendiente_autorizacion', 'mantenimiento', ?)
        """,
        (
            numero_pedido,
            proveedor_id,
            departamento_id,
            departamento_id,
            orden['subdepartamento_id'],
            orden['punto_venta_id'],
            request.form.get('fecha_entrega_compra') or None,
            texto_obs,
            usuario,
            usuario,
            empresa_id,
        ),
    )
    pedido_id = cur.lastrowid
    conn.execute(
        """
        INSERT INTO lineas_pedido
        (pedido_id, producto_id, cantidad_solicitada, precio_unitario)
        VALUES (?, ?, ?, ?)
        """,
        (pedido_id, producto_id, cantidad, precio),
    )
    conn.execute(
        """
        INSERT INTO mantenimiento_pedidos_compra
        (orden_id, repuesto_id, pedido_id, producto_id, proveedor_id, cantidad,
         estado, observaciones, usuario, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, 'solicitado', ?, ?, ?)
        """,
        (orden_id, repuesto["id"], pedido_id, producto_id, proveedor_id, cantidad, observaciones or None, usuario, empresa_id),
    )
    conn.execute(
        """
        UPDATE ordenes_trabajo
        SET pedido_compra_id = COALESCE(pedido_compra_id, ?),
            observaciones = TRIM(COALESCE(observaciones, '') || CHAR(10) || ?)
        WHERE id = ? AND empresa_id = ?
        """,
        (pedido_id, f"Pedido de compra generado: {numero_pedido}", orden_id, empresa_id),
    )
    conn.execute(
        """
        INSERT INTO historial_mantenimiento
        (equipo_id, fecha, tipo, orden_id, descripcion, tecnico, observaciones, empresa_id)
        VALUES (?, ?, 'solicitud_compra', ?, ?, ?, ?, ?)
        """,
        (
            orden['equipo_id'],
            datetime.now(),
            orden_id,
            f"Solicitud de compra {numero_pedido}: {cantidad:g} {repuesto['unidad'] or 'ud'} de {repuesto['nombre']}",
            usuario,
            observaciones or None,
            empresa_id,
        ),
    )
    conn.commit()
    conn.close()
    flash(f'Pedido {numero_pedido} creado y enviado a autorizacion de compras.', 'success')
    return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))


@mantenimiento_bp.route('/sincronizar-compras', methods=['POST'])
@login_required
def sincronizar_compras_mantenimiento():
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    usuario = session.get('user_nombre') or session.get('user_email') or 'admin'
    if not (_table_exists(conn, "mantenimiento_pedidos_compra") and _table_exists(conn, "recepciones") and _table_exists(conn, "lineas_recepcion")):
        conn.close()
        flash('No existe todavia el circuito de recepciones de compras.', 'warning')
        return redirect(url_for('mantenimiento.dashboard'))

    solicitudes = conn.execute(
        """
        SELECT mpc.*, r.nombre AS repuesto_nombre, r.codigo AS repuesto_codigo,
               r.unidad AS repuesto_unidad, ot.equipo_id
        FROM mantenimiento_pedidos_compra mpc
        JOIN repuestos r ON r.id = mpc.repuesto_id
        JOIN ordenes_trabajo ot ON ot.id = mpc.orden_id
        WHERE mpc.empresa_id = ?
          AND COALESCE(mpc.estado, 'solicitado') <> 'recibido'
        ORDER BY mpc.id
        """,
        (empresa_id,),
    ).fetchall()
    aplicadas = 0
    for solicitud in solicitudes:
        recibido = conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(lr.cantidad_recibida, 0)), 0) AS total
            FROM lineas_recepcion lr
            JOIN recepciones rec ON rec.id = lr.recepcion_id
            WHERE rec.empresa_id = ?
              AND rec.pedido_id = ?
              AND lr.producto_id = ?
              AND COALESCE(rec.stock_aplicado, COALESCE(rec.validada_operativamente, 0)) = 1
            """,
            (empresa_id, solicitud['pedido_id'], solicitud['producto_id']),
        ).fetchone()["total"] or 0
        recibido = _to_float(recibido, 0.0)
        aplicado = _to_float(solicitud['cantidad_recibida_aplicada'], 0.0)
        diferencia = round(recibido - aplicado, 3)
        if diferencia <= 0:
            continue
        conn.execute(
            """
            UPDATE repuestos
            SET stock_actual = COALESCE(stock_actual, 0) + ?
            WHERE id = ? AND empresa_id = ?
            """,
            (diferencia, solicitud['repuesto_id'], empresa_id),
        )
        nuevo_aplicado = round(aplicado + diferencia, 3)
        estado = 'recibido' if nuevo_aplicado + 0.0001 >= _to_float(solicitud['cantidad'], 0.0) else 'recibido_parcial'
        conn.execute(
            """
            UPDATE mantenimiento_pedidos_compra
            SET cantidad_recibida_aplicada = ?, estado = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (nuevo_aplicado, estado, solicitud['id'], empresa_id),
        )
        conn.execute(
            """
            INSERT INTO historial_mantenimiento
            (equipo_id, fecha, tipo, orden_id, descripcion, tecnico, observaciones, empresa_id)
            VALUES (?, ?, 'entrada_repuesto', ?, ?, ?, ?, ?)
            """,
            (
                solicitud['equipo_id'],
                datetime.now(),
                solicitud['orden_id'],
                f"Entrada de compra aplicada a repuesto: {solicitud['repuesto_codigo']} - {solicitud['repuesto_nombre']} ({diferencia:g} {solicitud['repuesto_unidad'] or 'ud'})",
                usuario,
                f"Pedido #{solicitud['pedido_id']}",
                empresa_id,
            ),
        )
        aplicadas += 1

    conn.commit()
    conn.close()
    flash(f'Sincronizacion de compras de mantenimiento finalizada. Entradas aplicadas: {aplicadas}.', 'success')
    return redirect(url_for('mantenimiento.dashboard'))

@mantenimiento_bp.route('/ordenes/<int:orden_id>/cambiar-estado/<string:estado>', methods=['POST'])
@login_required
def cambiar_estado_orden(orden_id, estado):
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    
    now = datetime.now()
    
    if estado == 'en_proceso':
        conn.execute("UPDATE ordenes_trabajo SET estado = ?, fecha_inicio = ? WHERE id = ? AND empresa_id = ?", (estado, now, orden_id, empresa_id))
    elif estado == 'completada':
        conn.close()
        flash('Para completar la orden debes registrar diagnostico y trabajo realizado.', 'warning')
        return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))
    else:
        conn.execute("UPDATE ordenes_trabajo SET estado = ? WHERE id = ? AND empresa_id = ?", (estado, orden_id, empresa_id))
    
    conn.commit()
    conn.close()
    
    flash(f'✅ Estado actualizado a {estado}', 'success')
    return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))

@mantenimiento_bp.route('/ordenes/<int:orden_id>/cerrar', methods=['POST'])
@login_required
def cerrar_orden(orden_id):
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    usuario = session.get('user_nombre') or session.get('user_email') or 'admin'

    orden = conn.execute(
        """
        SELECT *
        FROM ordenes_trabajo
        WHERE id = ? AND empresa_id = ?
        """,
        (orden_id, empresa_id),
    ).fetchone()

    if not orden:
        conn.close()
        flash('Orden no encontrada', 'danger')
        return redirect(url_for('mantenimiento.lista_ordenes'))

    if orden['estado'] in ('completada', 'cancelada'):
        conn.close()
        flash('Esta orden ya esta cerrada.', 'warning')
        return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))

    diagnostico = (request.form.get('diagnostico') or '').strip()
    trabajo_realizado = (request.form.get('trabajo_realizado') or '').strip()
    resultado = (request.form.get('resultado') or 'resuelta').strip()
    observaciones = (request.form.get('observaciones') or '').strip()
    horas_reales = _to_float_or_none(request.form.get('horas_reales'))
    coste_real = _to_float_or_none(request.form.get('coste_real'))
    requiere_seguimiento = 1 if request.form.get('requiere_seguimiento') else 0

    if not trabajo_realizado:
        conn.close()
        flash('Indica el trabajo realizado antes de cerrar la orden.', 'warning')
        return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))

    now = datetime.now()
    descripcion_historial = trabajo_realizado
    if diagnostico:
        descripcion_historial = f"{diagnostico}\n\n{trabajo_realizado}"

    conn.execute(
        """
        UPDATE ordenes_trabajo
        SET estado = 'completada',
            fecha_fin = ?,
            fecha_cierre = ?,
            cerrado_por = ?,
            diagnostico = ?,
            trabajo_realizado = ?,
            resultado = ?,
            observaciones = ?,
            horas_reales = ?,
            coste_real = ?,
            requiere_seguimiento = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (
            now,
            now,
            usuario,
            diagnostico,
            trabajo_realizado,
            resultado,
            observaciones,
            horas_reales,
            coste_real,
            requiere_seguimiento,
            orden_id,
            empresa_id,
        ),
    )

    conn.execute(
        """
        INSERT INTO historial_mantenimiento (
            equipo_id, fecha, tipo, orden_id, descripcion, tecnico, horas, coste, observaciones, empresa_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            orden['equipo_id'],
            now,
            orden['tipo'] or 'correctivo',
            orden_id,
            descripcion_historial,
            usuario,
            horas_reales,
            coste_real,
            observaciones,
            empresa_id,
        ),
    )

    if orden['incidencia_id']:
        conn.execute(
            """
            UPDATE incidencias
            SET estado = 'resuelta',
                solucion = ?,
                fecha_resolucion = ?,
                resuelto_por = ?,
                orden_trabajo_id = COALESCE(orden_trabajo_id, ?)
            WHERE id = ? AND empresa_id = ?
            """,
            (
                descripcion_historial,
                now,
                usuario,
                orden_id,
                orden['incidencia_id'],
                empresa_id,
            ),
        )

    if (orden['origen_tipo'] or '') == 'preventivo' and orden['origen_id']:
        tarea = conn.execute(
            """
            SELECT *
            FROM mantenimiento_preventivo
            WHERE id = ? AND empresa_id = ?
            """,
            (orden['origen_id'], empresa_id),
        ).fetchone()
        if tarea:
            hoy = datetime.now().date()
            conn.execute(
                """
                UPDATE mantenimiento_preventivo
                SET ultima_ejecucion = ?, proxima_ejecucion = ?
                WHERE id = ? AND empresa_id = ?
                """,
                (hoy, _proxima_fecha_preventivo(tarea, hoy), tarea['id'], empresa_id),
            )

    conn.commit()
    conn.close()
    flash('Orden cerrada con trazabilidad de solucion.', 'success')
    return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))


# ============================================
# MANTENIMIENTO PREVENTIVO
# ============================================
@mantenimiento_bp.route('/preventivo')
@login_required
def lista_preventivo():
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    
    tareas = conn.execute("""
        SELECT mp.*, e.nombre as equipo_nombre
        FROM mantenimiento_preventivo mp
        JOIN equipos_mantenimiento e ON mp.equipo_id = e.id
        WHERE mp.activo = 1 AND e.empresa_id = ?
        ORDER BY mp.proxima_ejecucion
    """, (empresa_id,)).fetchall()
    
    conn.close()
    return render_template('mantenimiento/preventivo.html', tareas=tareas)

@mantenimiento_bp.route('/preventivo/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_preventivo():
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    
    if request.method == 'POST':
        equipo_id = request.form['equipo_id']
        tarea = request.form['tarea']
        frecuencia = request.form['frecuencia']
        intervalo_dias = request.form.get('intervalo_dias')
        responsable = request.form.get('responsable', '')
        instrucciones = request.form.get('instrucciones', '')
        proxima_ejecucion = request.form.get('proxima_ejecucion')
        
        conn.execute("""
            INSERT INTO mantenimiento_preventivo 
            (equipo_id, tarea, frecuencia, intervalo_dias, responsable, instrucciones, proxima_ejecucion, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (equipo_id, tarea, frecuencia, intervalo_dias, responsable, instrucciones, proxima_ejecucion, empresa_id))
        
        conn.commit()
        conn.close()
        
        flash('✅ Tarea preventiva creada', 'success')
        return redirect(url_for('mantenimiento.lista_preventivo'))
    
    equipos = conn.execute("SELECT id, nombre FROM equipos_mantenimiento WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    conn.close()
    
    return render_template('mantenimiento/nuevo_preventivo.html', equipos=equipos)

@mantenimiento_bp.route('/preventivo/<int:tarea_id>/completar', methods=['POST'])
@login_required
def completar_preventivo(tarea_id):
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    
    tarea = conn.execute("SELECT * FROM mantenimiento_preventivo WHERE id = ? AND empresa_id = ?", 
                        (tarea_id, empresa_id)).fetchone()
    
    if tarea:
        hoy = datetime.now().date()
        proxima = _proxima_fecha_preventivo(tarea, hoy)
        
        conn.execute("""
            UPDATE mantenimiento_preventivo 
            SET ultima_ejecucion = ?, proxima_ejecucion = ?
            WHERE id = ?
        """, (hoy, proxima, tarea_id))
        
        conn.execute("""
            INSERT INTO historial_mantenimiento (equipo_id, tipo, descripcion, tecnico, fecha, empresa_id)
            VALUES (?, 'preventivo', ?, ?, ?, ?)
        """, (tarea['equipo_id'], tarea['tarea'], session.get('user_nombre', 'admin'), hoy, empresa_id))
        
        conn.commit()
        flash('✅ Mantenimiento preventivo completado', 'success')
    
    else:
        flash('❌ Tarea no encontrada', 'danger')
    
    conn.close()
    return redirect(url_for('mantenimiento.lista_preventivo'))


@mantenimiento_bp.route('/preventivo/<int:tarea_id>/generar-ot', methods=['POST'])
@login_required
def generar_ot_preventivo(tarea_id):
    conn = get_db()
    _ensure_mantenimiento_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    tarea = conn.execute(
        """
        SELECT mp.*, e.departamento_id, e.subdepartamento_id, e.punto_venta_id,
               e.ubicacion, e.habitacion_id, e.zona_id, e.nombre AS equipo_nombre
        FROM mantenimiento_preventivo mp
        JOIN equipos_mantenimiento e ON e.id = mp.equipo_id
        WHERE mp.id = ? AND mp.empresa_id = ? AND e.empresa_id = ?
        """,
        (tarea_id, empresa_id, empresa_id),
    ).fetchone()
    if not tarea:
        conn.close()
        flash('Tarea preventiva no encontrada.', 'warning')
        return redirect(url_for('mantenimiento.lista_preventivo'))

    abierta = conn.execute(
        """
        SELECT id, numero_orden
        FROM ordenes_trabajo
        WHERE empresa_id = ?
          AND origen_modulo = 'mantenimiento'
          AND origen_tipo = 'preventivo'
          AND origen_id = ?
          AND estado NOT IN ('completada', 'cancelada')
        ORDER BY id DESC
        LIMIT 1
        """,
        (empresa_id, tarea_id),
    ).fetchone()
    if abierta:
        conn.close()
        flash(f"Ya existe una OT preventiva abierta: {abierta['numero_orden']}.", 'warning')
        return redirect(url_for('mantenimiento.ver_orden', orden_id=abierta['id']))

    numero_orden = generar_numero_ot_conn(conn)
    descripcion = f"Preventivo: {tarea['tarea']}\n\nEquipo: {tarea['equipo_nombre']}"
    if tarea['instrucciones']:
        descripcion += f"\n\nInstrucciones:\n{tarea['instrucciones']}"
    cur = conn.execute(
        """
        INSERT INTO ordenes_trabajo
        (numero_orden, equipo_id, tipo, prioridad, estado, descripcion, tecnico_asignado,
         origen_modulo, origen_tipo, origen_id, departamento_id, subdepartamento_id,
         punto_venta_id, ubicacion, habitacion_id, zona_id, empresa_id)
        VALUES (?, ?, 'preventivo', 'media', 'abierta', ?, ?, 'mantenimiento',
                'preventivo', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            numero_orden,
            tarea['equipo_id'],
            descripcion,
            tarea['responsable'] or '',
            tarea_id,
            tarea['departamento_id'],
            tarea['subdepartamento_id'],
            tarea['punto_venta_id'],
            tarea['ubicacion'],
            tarea['habitacion_id'],
            tarea['zona_id'],
            empresa_id,
        ),
    )
    orden_id = cur.lastrowid
    conn.commit()
    conn.close()
    flash(f'OT preventiva creada: {numero_orden}.', 'success')
    return redirect(url_for('mantenimiento.ver_orden', orden_id=orden_id))

# ============================================
# REPUESTOS
# ============================================
@mantenimiento_bp.route('/repuestos')
@login_required
def lista_repuestos():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    repuestos = conn.execute("""
        SELECT r.*, p.nombre as proveedor_nombre
        FROM repuestos r
        LEFT JOIN proveedores p ON r.proveedor_id = p.id
        WHERE r.activo = 1 AND r.empresa_id = ?
        ORDER BY r.nombre
    """, (empresa_id,)).fetchall()
    
    conn.close()
    return render_template('mantenimiento/repuestos.html', repuestos=repuestos)

@mantenimiento_bp.route('/repuestos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_repuesto():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    if request.method == 'POST':
        codigo = request.form['codigo']
        nombre = request.form['nombre']
        descripcion = request.form.get('descripcion', '')
        proveedor_id = request.form.get('proveedor_id')
        referencia_proveedor = request.form.get('referencia_proveedor', '')
        stock_actual = float(request.form.get('stock_actual', 0))
        stock_minimo = float(request.form.get('stock_minimo', 0))
        precio_compra = float(request.form.get('precio_compra', 0))
        unidad = request.form.get('unidad', 'ud')
        ubicacion_almacen = request.form.get('ubicacion_almacen', '')
        compatible_con = request.form.get('compatible_con', '')
        
        conn.execute("""
            INSERT INTO repuestos 
            (codigo, nombre, descripcion, proveedor_id, referencia_proveedor, 
             stock_actual, stock_minimo, precio_compra, unidad, ubicacion_almacen, 
             compatible_con, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (codigo, nombre, descripcion, proveedor_id, referencia_proveedor,
              stock_actual, stock_minimo, precio_compra, unidad, ubicacion_almacen,
              compatible_con, empresa_id))
        
        conn.commit()
        conn.close()
        
        flash('✅ Repuesto creado correctamente', 'success')
        return redirect(url_for('mantenimiento.lista_repuestos'))
    
    proveedores = conn.execute("SELECT id, nombre FROM proveedores WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    conn.close()
    
    return render_template('mantenimiento/nuevo_repuesto.html', proveedores=proveedores)

@mantenimiento_bp.route('/repuestos/<int:repuesto_id>')
@login_required
def ver_repuesto(repuesto_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    repuesto = conn.execute("""
        SELECT r.*, p.nombre as proveedor_nombre
        FROM repuestos r
        LEFT JOIN proveedores p ON r.proveedor_id = p.id
        WHERE r.id = ? AND r.empresa_id = ?
    """, (repuesto_id, empresa_id)).fetchone()
    
    if not repuesto:
        flash('Repuesto no encontrado', 'danger')
        return redirect(url_for('mantenimiento.lista_repuestos'))
    
    historial = conn.execute("""
        SELECT orp.*, ot.numero_orden, ot.fecha_apertura as fecha,
               e.nombre as equipo_nombre
        FROM ordenes_repuestos orp
        JOIN ordenes_trabajo ot ON orp.orden_id = ot.id
        JOIN equipos_mantenimiento e ON ot.equipo_id = e.id
        WHERE orp.repuesto_id = ?
        ORDER BY ot.fecha_apertura DESC
    """, (repuesto_id,)).fetchall()
    
    conn.close()
    
    return render_template('mantenimiento/ver_repuesto.html', 
                         repuesto=repuesto, 
                         historial=historial)

@mantenimiento_bp.route('/repuestos/<int:repuesto_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_repuesto(repuesto_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    if request.method == 'POST':
        codigo = request.form['codigo']
        nombre = request.form['nombre']
        descripcion = request.form.get('descripcion', '')
        proveedor_id = request.form.get('proveedor_id')
        referencia_proveedor = request.form.get('referencia_proveedor', '')
        stock_actual = float(request.form.get('stock_actual', 0))
        stock_minimo = float(request.form.get('stock_minimo', 0))
        precio_compra = float(request.form.get('precio_compra', 0))
        unidad = request.form.get('unidad', 'ud')
        ubicacion_almacen = request.form.get('ubicacion_almacen', '')
        compatible_con = request.form.get('compatible_con', '')
        
        conn.execute("""
            UPDATE repuestos SET
                codigo = ?, nombre = ?, descripcion = ?, proveedor_id = ?,
                referencia_proveedor = ?, stock_actual = ?, stock_minimo = ?,
                precio_compra = ?, unidad = ?, ubicacion_almacen = ?,
                compatible_con = ?
            WHERE id = ? AND empresa_id = ?
        """, (codigo, nombre, descripcion, proveedor_id, referencia_proveedor,
              stock_actual, stock_minimo, precio_compra, unidad, ubicacion_almacen,
              compatible_con, repuesto_id, empresa_id))
        
        conn.commit()
        conn.close()
        
        flash('✅ Repuesto actualizado correctamente', 'success')
        return redirect(url_for('mantenimiento.ver_repuesto', repuesto_id=repuesto_id))
    
    repuesto = conn.execute("SELECT * FROM repuestos WHERE id = ? AND empresa_id = ?", (repuesto_id, empresa_id)).fetchone()
    
    if not repuesto:
        flash('Repuesto no encontrado', 'danger')
        return redirect(url_for('mantenimiento.lista_repuestos'))
    
    proveedores = conn.execute("SELECT id, nombre FROM proveedores WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    conn.close()
    
    return render_template('mantenimiento/editar_repuesto.html', 
                         repuesto=repuesto, 
                         proveedores=proveedores)

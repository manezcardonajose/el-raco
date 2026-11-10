from flask import render_template, request, redirect, url_for, session, flash, send_file
from database import get_db
from datetime import datetime
from io import BytesIO
import pandas as pd
from . import incidencias_bp
from blueprints.auth.routes import login_required
from helpers.operativa_helper import ensure_incidencias_context_schema, ensure_mantenimiento_context_schema


def _next_local_url(value):
    if value and value.startswith('/') and not value.startswith('//'):
        return value
    return None


def _numero_ot_incidencia(conn):
    fecha = datetime.now().strftime("%Y%m%d")
    hoy = datetime.now().strftime("%Y-%m-%d")
    total = conn.execute(
        "SELECT COUNT(*) AS total FROM ordenes_trabajo WHERE fecha_apertura LIKE ?",
        (f"{hoy}%",),
    ).fetchone()["total"] or 0
    for extra in range(1, 1000):
        numero = f"OT-{fecha}-{total + extra:03d}"
        existe = conn.execute("SELECT id FROM ordenes_trabajo WHERE numero_orden = ?", (numero,)).fetchone()
        if not existe:
            return numero
    return f"OT-{fecha}-{int(datetime.now().timestamp())}"


def _equipo_generico_incidencia(conn, incidencia, empresa_id):
    ubicacion = incidencia["ubicacion"] or incidencia["observaciones"] or f"Incidencia #{incidencia['id']}"
    codigo = f"INC-{incidencia['id']}"
    equipo = conn.execute(
        "SELECT id FROM equipos_mantenimiento WHERE empresa_id = ? AND COALESCE(codigo, '') = ? LIMIT 1",
        (empresa_id, codigo),
    ).fetchone()
    if equipo:
        return equipo["id"]
    cursor = conn.execute(
        """
        INSERT INTO equipos_mantenimiento
        (codigo, nombre, tipo, familia, categoria_operativa, clase_activo, criticidad,
         inventariable, requiere_appcc, ubicacion, departamento_id, subdepartamento_id,
         punto_venta_id, origen_modulo, habitacion_id, zona_id, activo, empresa_id)
        VALUES (?, ?, 'incidencia', ?, 'mantenimiento', 'ubicacion', ?, 1, 0, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            codigo,
            f"{ubicacion} - incidencia",
            incidencia["origen_modulo"] or "general",
            incidencia["gravedad"] or "media",
            ubicacion,
            incidencia["departamento_id"],
            incidencia["subdepartamento_id"],
            incidencia["punto_venta_id"],
            incidencia["origen_modulo"] or "incidencias",
            incidencia["habitacion_id"],
            incidencia["zona_id"],
            empresa_id,
        ),
    )
    return cursor.lastrowid


DESTINOS_INCIDENCIA = {
    "interno": {"label": "Registro interno", "requiere_mantenimiento": 0, "requiere_appcc": 0},
    "pisos": {"label": "Pisos", "requiere_mantenimiento": 0, "requiere_appcc": 0},
    "mantenimiento": {"label": "Mantenimiento", "requiere_mantenimiento": 1, "requiere_appcc": 0},
    "compras": {"label": "Compras", "requiere_mantenimiento": 0, "requiere_appcc": 0},
    "appcc": {"label": "APPCC", "requiere_mantenimiento": 0, "requiere_appcc": 1},
    "recepcion": {"label": "Recepcion", "requiere_mantenimiento": 0, "requiere_appcc": 0},
    "rrhh": {"label": "RRHH", "requiere_mantenimiento": 0, "requiere_appcc": 0},
}


CATEGORIAS_INCIDENCIA = {
    "mantenimiento": "Mantenimiento",
    "rotura": "Rotura",
    "seguridad": "Seguridad",
    "limpieza": "Limpieza",
    "falta_material": "Falta material",
    "recepcion_compra": "Recepcion de compras",
    "precio_compra": "Precio compra",
    "appcc_alimentario": "APPCC alimentario",
    "temperatura": "Temperatura",
    "caducidad": "Caducidad",
    "documentacion": "Documentacion",
    "registro_interno": "Registro interno",
    "otro": "Otro",
}

# ============================================
# LISTA DE INCIDENCIAS
# ============================================
@incidencias_bp.route('/')
@login_required
def lista():
    conn = get_db()
    ensure_incidencias_context_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    
    estado = request.args.get('estado', '')
    tipo = request.args.get('tipo', '')
    gravedad = request.args.get('gravedad', '')
    fecha_desde = request.args.get('fecha_desde', '')
    fecha_hasta = request.args.get('fecha_hasta', '')
    
    query = "SELECT * FROM incidencias WHERE empresa_id = ?"
    params = [empresa_id]
    
    if estado:
        query += " AND estado = ?"
        params.append(estado)
    if tipo:
        query += " AND tipo = ?"
        params.append(tipo)
    if gravedad:
        query += " AND gravedad = ?"
        params.append(gravedad)
    if fecha_desde:
        query += " AND fecha >= ?"
        params.append(fecha_desde)
    if fecha_hasta:
        query += " AND fecha <= ?"
        params.append(fecha_hasta)
    
    query += " ORDER BY fecha DESC"
    
    incidencias = conn.execute(query, params).fetchall()
    
    total_abiertas = conn.execute("""
        SELECT COUNT(*) as total FROM incidencias 
        WHERE estado = 'abierta' AND empresa_id = ?
    """, (empresa_id,)).fetchone()['total']
    
    total_en_proceso = conn.execute("""
        SELECT COUNT(*) as total FROM incidencias 
        WHERE estado = 'en_proceso' AND empresa_id = ?
    """, (empresa_id,)).fetchone()['total']
    
    total_resueltas = conn.execute("""
        SELECT COUNT(*) as total FROM incidencias 
        WHERE estado = 'resuelta' AND empresa_id = ?
    """, (empresa_id,)).fetchone()['total']
    
    tipos = conn.execute("""
        SELECT tipo, COUNT(*) as total 
        FROM incidencias 
        WHERE empresa_id = ? 
        GROUP BY tipo
        ORDER BY total DESC
    """, (empresa_id,)).fetchall()
    
    conn.close()
    
    return render_template('incidencias/incidencias.html',
                         incidencias=incidencias,
                         total_abiertas=total_abiertas,
                         total_en_proceso=total_en_proceso,
                         total_resueltas=total_resueltas,
                         tipos=tipos,
                         filtros={'estado': estado, 'tipo': tipo, 'gravedad': gravedad,
                                 'fecha_desde': fecha_desde, 'fecha_hasta': fecha_hasta})

# ============================================
# VER INCIDENCIA
# ============================================
@incidencias_bp.route('/<int:incidencia_id>')
@login_required
def ver(incidencia_id):
    conn = get_db()
    ensure_incidencias_context_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    next_url = _next_local_url(request.args.get('next'))
    
    incidencia = conn.execute("""
        SELECT * FROM incidencias 
        WHERE id = ? AND empresa_id = ?
    """, (incidencia_id, empresa_id)).fetchone()
    conn.close()
    
    if not incidencia:
        flash(' Incidencia no encontrada', 'danger')
        return redirect(url_for('incidencias.lista'))
    
    return render_template(
        'incidencias/ver_incidencia.html',
        incidencia=incidencia,
        next_url=next_url,
        destinos=DESTINOS_INCIDENCIA,
        categorias=CATEGORIAS_INCIDENCIA,
    )

# ============================================
# RESOLVER INCIDENCIA
# ============================================
@incidencias_bp.route('/<int:incidencia_id>/resolver', methods=["POST"])
@login_required
def resolver(incidencia_id):
    solucion = request.form["solucion"]
    empresa_id = session.get('user_empresa_id', 1)
    next_url = _next_local_url(request.form.get('next'))
    
    conn = get_db()
    ensure_incidencias_context_schema(conn)
    conn.execute("""
        UPDATE incidencias 
        SET estado = 'resuelta', 
            solucion = ?, 
            fecha_resolucion = CURRENT_TIMESTAMP, 
            resuelto_por = ?
        WHERE id = ? AND empresa_id = ?
    """, (solucion, session.get('user_nombre', 'admin'), incidencia_id, empresa_id))
    conn.commit()
    conn.close()
    
    flash(' Incidencia resuelta', 'success')
    if next_url:
        return redirect(next_url)
    return redirect(url_for('incidencias.lista'))


@incidencias_bp.route('/<int:incidencia_id>/clasificar', methods=["POST"])
@login_required
def clasificar(incidencia_id):
    empresa_id = session.get('user_empresa_id', 1)
    next_url = _next_local_url(request.form.get('next'))
    destino = (request.form.get('destino_modulo') or '').strip()
    categoria = (request.form.get('categoria_operativa') or '').strip()
    gravedad = (request.form.get('gravedad') or '').strip()
    ubicacion = (request.form.get('ubicacion') or '').strip()
    if destino not in DESTINOS_INCIDENCIA:
        flash('Destino de incidencia no valido.', 'warning')
        return redirect(next_url or url_for('incidencias.ver', incidencia_id=incidencia_id))
    if categoria and categoria not in CATEGORIAS_INCIDENCIA:
        categoria = 'otro'
    if gravedad and gravedad not in {'baja', 'media', 'alta', 'urgente', 'critica'}:
        gravedad = ''

    reglas = DESTINOS_INCIDENCIA[destino]
    conn = get_db()
    ensure_incidencias_context_schema(conn)
    incidencia = conn.execute(
        "SELECT id FROM incidencias WHERE id = ? AND empresa_id = ?",
        (incidencia_id, empresa_id),
    ).fetchone()
    if not incidencia:
        conn.close()
        flash('Incidencia no encontrada.', 'danger')
        return redirect(url_for('incidencias.lista'))

    sets = [
        "destino_modulo = ?",
        "requiere_mantenimiento = ?",
        "requiere_appcc = ?",
    ]
    params = [destino, reglas["requiere_mantenimiento"], reglas["requiere_appcc"]]
    if categoria:
        sets.append("categoria_operativa = ?")
        params.append(categoria)
    if gravedad:
        sets.append("gravedad = ?")
        params.append(gravedad)
    if ubicacion:
        sets.append("ubicacion = ?")
        params.append(ubicacion)
    sets.append("observaciones = TRIM(COALESCE(observaciones, '') || ?)")
    params.append(f"\nClasificada a {DESTINOS_INCIDENCIA[destino]['label']} por {session.get('user_nombre') or 'usuario'} el {datetime.now().strftime('%Y-%m-%d %H:%M')}.")
    params.extend([incidencia_id, empresa_id])
    conn.execute(
        f"UPDATE incidencias SET {', '.join(sets)} WHERE id = ? AND empresa_id = ?",
        params,
    )
    conn.commit()
    conn.close()
    flash(f"Incidencia enviada a {DESTINOS_INCIDENCIA[destino]['label']}.", "success")
    return redirect(next_url or url_for('incidencias.ver', incidencia_id=incidencia_id))


@incidencias_bp.route('/<int:incidencia_id>/crear-ot', methods=["POST"])
@login_required
def crear_ot_mantenimiento(incidencia_id):
    empresa_id = session.get('user_empresa_id', 1)
    next_url = _next_local_url(request.form.get('next'))
    conn = get_db()
    ensure_incidencias_context_schema(conn)
    ensure_mantenimiento_context_schema(conn)
    incidencia = conn.execute(
        "SELECT * FROM incidencias WHERE id = ? AND empresa_id = ?",
        (incidencia_id, empresa_id),
    ).fetchone()
    if not incidencia:
        conn.close()
        flash('Incidencia no encontrada', 'danger')
        return redirect(next_url or url_for('incidencias.lista'))
    if incidencia["orden_trabajo_id"]:
        conn.close()
        flash('La incidencia ya tiene orden de mantenimiento.', 'info')
        return redirect(next_url or url_for('incidencias.ver', incidencia_id=incidencia_id))

    equipo_id = incidencia["equipo_mantenimiento_id"] or _equipo_generico_incidencia(conn, incidencia, empresa_id)
    numero_orden = _numero_ot_incidencia(conn)
    descripcion = (
        f"Incidencia #{incidencia_id} enviada a mantenimiento.\n"
        f"Origen: {incidencia['origen_modulo'] or incidencia['tipo']}\n"
        f"Ubicacion: {incidencia['ubicacion'] or incidencia['observaciones'] or '-'}\n"
        f"Detalle: {incidencia['descripcion']}"
    )
    cursor = conn.execute(
        """
        INSERT INTO ordenes_trabajo
        (numero_orden, equipo_id, tipo, prioridad, estado, descripcion, tecnico_asignado,
         incidencia_id, observaciones, origen_modulo, origen_tipo, origen_id,
         departamento_id, subdepartamento_id, punto_venta_id, ubicacion, habitacion_id,
         zona_id, requiere_appcc, empresa_id)
        VALUES (?, ?, 'correctivo', ?, 'abierta', ?, '', ?, 'Creada desde ficha de incidencia',
                ?, 'incidencia', ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            numero_orden,
            equipo_id,
            incidencia["gravedad"] or "media",
            descripcion,
            incidencia_id,
            incidencia["origen_modulo"] or "incidencias",
            incidencia_id,
            incidencia["departamento_id"],
            incidencia["subdepartamento_id"],
            incidencia["punto_venta_id"],
            incidencia["ubicacion"],
            incidencia["habitacion_id"],
            incidencia["zona_id"],
            empresa_id,
        ),
    )
    orden_id = cursor.lastrowid
    conn.execute(
        """
        UPDATE incidencias
        SET destino_modulo = 'mantenimiento',
            requiere_mantenimiento = 1,
            requiere_appcc = 0,
            equipo_mantenimiento_id = ?,
            orden_trabajo_id = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (equipo_id, orden_id, incidencia_id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash(f'Orden {numero_orden} creada en mantenimiento.', 'success')
    return redirect(next_url or url_for('mantenimiento.ver_orden', orden_id=orden_id))

# ============================================
# EXPORTAR INCIDENCIAS
# ============================================
@incidencias_bp.route('/exportar')
@login_required
def exportar():
    """Exporta el listado de incidencias a Excel"""
    conn = get_db()
    ensure_incidencias_context_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    
    datos = conn.execute("""
        SELECT 
            id,
            fecha,
            tipo,
            descripcion,
            gravedad,
            estado,
            usuario,
            solucion,
            fecha_resolucion,
            resuelto_por
        FROM incidencias
        WHERE empresa_id = ?
        ORDER BY fecha DESC
    """, (empresa_id,)).fetchall()
    
    conn.close()
    
    df = pd.DataFrame([dict(row) for row in datos])
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Incidencias', index=False)
    
    output.seek(0)
    
    return send_file(
        output,
        download_name=f'incidencias_{datetime.now().strftime("%Y%m%d")}.xlsx',
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


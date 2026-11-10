import os
import sqlite3
import uuid
from datetime import datetime

from flask import Response, jsonify, make_response, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

from database import get_db
from . import empresas_bp
from blueprints.auth.routes import login_required, permiso_requerido, usuario_tiene_permiso
from helpers.etiquetas_helper import (
    create_label_config_from_form,
    create_print_device_from_form,
    ensure_etiquetas_schema,
    get_label_config,
    get_label_configs,
    get_current_print_device,
    get_print_devices,
    update_print_device_from_form,
    update_label_config_from_form,
)
from helpers.impresion_helper import (
    api_get_device,
    api_pending_jobs,
    api_touch_device,
    create_print_job,
    ensure_print_queue_schema,
    get_job_for_device,
    list_print_jobs,
    update_job_status,
)
from helpers.label_raw_helper import zpl_label
from helpers.operativa_helper import ensure_equipos_unificados_schema
from helpers.centros_helper import asignar_centro_desde_departamento, centro_activo_id


LOGO_EXTENSIONES = {"png", "jpg", "jpeg", "webp", "gif"}
DEVICE_COOKIE = "el_raco_device_uid"


def _guardar_logo_empresa(archivo, empresa_id=None):
    if not archivo or not archivo.filename:
        return ""
    nombre_seguro = secure_filename(archivo.filename)
    if "." not in nombre_seguro:
        return ""
    extension = nombre_seguro.rsplit(".", 1)[1].lower()
    if extension not in LOGO_EXTENSIONES:
        return ""
    carpeta = os.path.join("static", "uploads", "empresas")
    os.makedirs(carpeta, exist_ok=True)
    prefijo = f"empresa_{empresa_id or 'nueva'}"
    nombre_final = secure_filename(f"{prefijo}_{nombre_seguro}")
    ruta = os.path.join(carpeta, nombre_final)
    archivo.save(ruta)
    return f"uploads/empresas/{nombre_final}"

# ============================================
# LISTA DE EMPRESAS
# ============================================
@empresas_bp.route('/')
@login_required
@permiso_requerido('gestionar_empresas')
def listar_empresas():
    conn = get_db()
    empresas = conn.execute("SELECT * FROM empresas ORDER BY nombre").fetchall()
    conn.close()
    return render_template('empresas/empresas.html', empresas=empresas)


@empresas_bp.route('/etiquetas', methods=['GET', 'POST'])
@login_required
@permiso_requerido('gestionar_empresas')
def configurar_etiquetas():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    ensure_etiquetas_schema(conn, empresa_id)

    if request.method == 'POST':
        accion = request.form.get('accion')
        try:
            if accion == 'crear':
                if create_label_config_from_form(conn, empresa_id, request.form):
                    conn.commit()
                    flash('Formato de etiqueta creado.', 'success')
                else:
                    flash('Indica codigo y nombre para crear el formato.', 'warning')
            else:
                if update_label_config_from_form(conn, empresa_id, request.form):
                    conn.commit()
                    flash('Configuracion de etiqueta actualizada.', 'success')
                else:
                    flash('No se encontro el formato a actualizar.', 'warning')
        except sqlite3.IntegrityError:
            conn.rollback()
            flash('Ya existe un formato con ese codigo.', 'danger')
        finally:
            conn.close()
        return redirect(url_for('empresas.configurar_etiquetas'))

    formatos = get_label_configs(conn, empresa_id)
    conn.close()
    return render_template('empresas/etiquetas_config.html', formatos=formatos)


def _rows_or_empty(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def _resumen_cola_por_dispositivo(conn, empresa_id):
    try:
        ensure_print_queue_schema(conn, empresa_id)
        centro_id = centro_activo_id()
        centro_sql = " AND centro_id = ?" if centro_id else ""
        centro_params = [centro_id] if centro_id else []
        rows = conn.execute(
            """
            SELECT device_uid, estado, COUNT(*) AS total
            FROM cola_impresion
            WHERE empresa_id = ?
              AND estado IN ('pendiente', 'procesando', 'error')
            """ + centro_sql + """
            GROUP BY device_uid, estado
            """,
            [empresa_id] + centro_params,
        ).fetchall()
    except Exception:
        return {}
    resumen = {}
    for row in rows:
        device_uid = row["device_uid"] or ""
        resumen.setdefault(device_uid, {"pendiente": 0, "procesando": 0, "error": 0})
        resumen[device_uid][row["estado"]] = row["total"]
    return resumen


def _to_int_or_none(value):
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def _codigo_activo_libre(conn, empresa_id, prefijo="ACT"):
    total = conn.execute(
        "SELECT COUNT(*) AS total FROM equipos_mantenimiento WHERE empresa_id = ?",
        (empresa_id,),
    ).fetchone()["total"] or 0
    for extra in range(1, 10000):
        codigo = f"{prefijo}-{total + extra:04d}"
        if not conn.execute(
            "SELECT 1 FROM equipos_mantenimiento WHERE empresa_id = ? AND codigo = ?",
            (empresa_id, codigo),
        ).fetchone():
            return codigo
    return f"{prefijo}-{int(datetime.now().timestamp())}"


def _crear_activo_mantenimiento(conn, empresa_id, form):
    nombre = (form.get("nombre") or "").strip()
    if not nombre:
        return None
    codigo = (form.get("codigo") or "").strip() or _codigo_activo_libre(conn, empresa_id)
    departamento_id = _to_int_or_none(form.get("departamento_id"))
    centro_id = asignar_centro_desde_departamento(conn, departamento_id)
    cur = conn.execute(
        """
        INSERT INTO equipos_mantenimiento (
            codigo, nombre, tipo, familia, categoria_operativa, clase_activo,
            criticidad, inventariable, requiere_appcc, origen_modulo,
            ubicacion, departamento_id, subdepartamento_id, punto_venta_id,
            marca, modelo, numero_serie, activo, empresa_id, centro_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            codigo,
            nombre,
            (form.get("tipo") or "general").strip(),
            (form.get("familia") or "general").strip(),
            (form.get("categoria_operativa") or "").strip(),
            (form.get("clase_activo") or "equipo").strip(),
            (form.get("criticidad") or "media").strip(),
            1 if form.get("inventariable", "1") else 0,
            1 if form.get("requiere_appcc") else 0,
            (form.get("origen_modulo") or "establecimiento").strip(),
            (form.get("ubicacion") or "").strip(),
            departamento_id,
            _to_int_or_none(form.get("subdepartamento_id")),
            _to_int_or_none(form.get("punto_venta_id")),
            (form.get("marca") or "").strip(),
            (form.get("modelo") or "").strip(),
            (form.get("numero_serie") or "").strip(),
            empresa_id,
            centro_id,
        ),
    )
    return cur.lastrowid


@empresas_bp.route('/equipos-activos', methods=['GET', 'POST'])
@login_required
def equipos_activos():
    if not (usuario_tiene_permiso('gestionar_empresas') or usuario_tiene_permiso('gestionar_estructura')):
        flash('No tienes permisos para acceder a esta pantalla.', 'warning')
        return redirect(url_for('home'))
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    ensure_etiquetas_schema(conn, empresa_id)
    ensure_equipos_unificados_schema(conn)
    if not any(row["name"] == "centro_id" for row in conn.execute("PRAGMA table_info(equipos_mantenimiento)").fetchall()):
        conn.execute("ALTER TABLE equipos_mantenimiento ADD COLUMN centro_id INTEGER")
    conn.commit()

    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'crear_activo':
            activo_id = _crear_activo_mantenimiento(conn, empresa_id, request.form)
            if activo_id:
                ensure_equipos_unificados_schema(conn)
                conn.commit()
                flash('Activo creado y vinculado al inventario operativo.', 'success')
            else:
                flash('Indica un nombre para crear el activo.', 'warning')
        elif accion == 'crear_desde_dispositivo':
            device_id = _to_int_or_none(request.form.get('device_id'))
            dispositivo = conn.execute(
                """
                SELECT *
                FROM config_dispositivos_impresion
                WHERE id = ? AND empresa_id = ?
                LIMIT 1
                """,
                (device_id, empresa_id),
            ).fetchone()
            if dispositivo:
                activo_id = _crear_activo_mantenimiento(conn, empresa_id, {
                    "nombre": dispositivo["nombre"],
                    "tipo": dispositivo["tipo_dispositivo"] or "dispositivo",
                    "familia": "tecnologia",
                    "categoria_operativa": "dispositivo",
                    "clase_activo": "dispositivo",
                    "criticidad": "media",
                    "inventariable": "1",
                    "origen_modulo": "impresion",
                    "ubicacion": dispositivo["ubicacion"] or "",
                    "departamento_id": dispositivo["departamento_id"],
                    "subdepartamento_id": dispositivo["subdepartamento_id"],
                    "punto_venta_id": dispositivo["punto_venta_id"],
                    "marca": "",
                    "modelo": "",
                    "numero_serie": dispositivo["device_uid"],
                })
                if activo_id:
                    conn.execute(
                        """
                        UPDATE config_dispositivos_impresion
                        SET equipo_mantenimiento_id = ?, updated_at = ?
                        WHERE id = ? AND empresa_id = ?
                        """,
                        (activo_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), device_id, empresa_id),
                    )
                    conn.commit()
                    flash('Dispositivo convertido en activo inventariable.', 'success')
            else:
                flash('Dispositivo no encontrado.', 'warning')
        conn.close()
        return redirect(url_for('empresas.equipos_activos'))

    q = (request.args.get('q') or '').strip()
    familia = (request.args.get('familia') or '').strip()
    estado = (request.args.get('estado') or 'activos').strip()
    centro_id = centro_activo_id()
    params = [empresa_id]
    where = ["em.empresa_id = ?"]
    if centro_id:
        where.append("em.centro_id = ?")
        params.append(centro_id)
    if estado == 'activos':
        where.append("COALESCE(em.activo, 1) = 1")
    elif estado == 'inactivos':
        where.append("COALESCE(em.activo, 1) = 0")
    if familia:
        where.append("COALESCE(em.familia, '') = ?")
        params.append(familia)
    if q:
        where.append("""
            (
                LOWER(COALESCE(em.nombre, '')) LIKE ?
                OR LOWER(COALESCE(em.codigo, '')) LIKE ?
                OR LOWER(COALESCE(em.ubicacion, '')) LIKE ?
                OR LOWER(COALESCE(em.numero_serie, '')) LIKE ?
            )
        """)
        like = f"%{q.lower()}%"
        params.extend([like, like, like, like])

    activos = conn.execute(f"""
        SELECT
            em.*,
            d.nombre AS departamento_nombre,
            s.nombre AS subdepartamento_nombre,
            pv.nombre AS punto_venta_nombre,
            eq.id AS appcc_id,
            eq.tipo AS appcc_tipo,
            eq.rango_min AS appcc_rango_min,
            eq.rango_max AS appcc_rango_max,
            a.nombre AS almacen_nombre,
            (SELECT COUNT(*) FROM ordenes_trabajo ot
             WHERE ot.equipo_id = em.id
               AND ot.empresa_id = em.empresa_id
               AND LOWER(COALESCE(ot.estado, '')) NOT IN ('cerrada', 'cerrado', 'finalizada', 'finalizado', 'cancelada', 'cancelado')) AS ot_abiertas,
            (SELECT COUNT(*) FROM config_dispositivos_impresion di
             WHERE di.equipo_mantenimiento_id = em.id
               AND di.empresa_id = em.empresa_id) AS dispositivos_vinculados
        FROM equipos_mantenimiento em
        LEFT JOIN departamentos d ON d.id = em.departamento_id
        LEFT JOIN subdepartamentos s ON s.id = em.subdepartamento_id
        LEFT JOIN puntos_venta pv ON pv.id = em.punto_venta_id
        LEFT JOIN equipos eq ON eq.id = em.equipo_appcc_id
        LEFT JOIN almacenes a ON a.id = em.almacen_id
        WHERE {" AND ".join(where)}
        ORDER BY COALESCE(em.activo, 1) DESC, em.familia, em.nombre
        LIMIT 500
    """, params).fetchall()

    resumen = {
        "total": conn.execute(
            "SELECT COUNT(*) AS total FROM equipos_mantenimiento WHERE empresa_id = ?" + (" AND centro_id = ?" if centro_id else ""),
            [empresa_id] + ([centro_id] if centro_id else []),
        ).fetchone()["total"] or 0,
        "appcc": conn.execute(
            "SELECT COUNT(*) AS total FROM equipos_mantenimiento WHERE empresa_id = ? AND COALESCE(requiere_appcc, 0) = 1" + (" AND centro_id = ?" if centro_id else ""),
            [empresa_id] + ([centro_id] if centro_id else []),
        ).fetchone()["total"] or 0,
        "almacenes": conn.execute(
            "SELECT COUNT(*) AS total FROM equipos_mantenimiento WHERE empresa_id = ? AND almacen_id IS NOT NULL" + (" AND centro_id = ?" if centro_id else ""),
            [empresa_id] + ([centro_id] if centro_id else []),
        ).fetchone()["total"] or 0,
        "dispositivos": conn.execute(
            "SELECT COUNT(*) AS total FROM config_dispositivos_impresion WHERE empresa_id = ?" + (" AND centro_id = ?" if centro_id else ""),
            [empresa_id] + ([centro_id] if centro_id else []),
        ).fetchone()["total"] or 0,
    }
    familia_params = [empresa_id]
    familia_centro_sql = ""
    if centro_id:
        familia_centro_sql = " AND centro_id = ?"
        familia_params.append(centro_id)
    familias = conn.execute(
        """
        SELECT COALESCE(NULLIF(familia, ''), 'general') AS familia, COUNT(*) AS total
        FROM equipos_mantenimiento
        WHERE empresa_id = ?
        """ + familia_centro_sql + """
        GROUP BY COALESCE(NULLIF(familia, ''), 'general')
        ORDER BY familia
        """,
        familia_params,
    ).fetchall()
    disp_params = [empresa_id]
    disp_centro_sql = ""
    if centro_id:
        disp_centro_sql = " AND centro_id = ?"
        disp_params.append(centro_id)
    dispositivos_sin_activo = conn.execute(
        """
        SELECT *
        FROM config_dispositivos_impresion
        WHERE empresa_id = ?
          AND equipo_mantenimiento_id IS NULL
        """ + disp_centro_sql + """
        ORDER BY nombre
        """,
        disp_params,
    ).fetchall()
    centro_sql = " AND centro_id = ?" if centro_id else ""
    centro_params = [centro_id] if centro_id else []
    departamentos = _rows_or_empty(conn, "SELECT id, nombre FROM departamentos WHERE (empresa_id = ? OR empresa_id IS NULL)" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params)
    subdepartamentos = _rows_or_empty(conn, "SELECT id, nombre FROM subdepartamentos WHERE (empresa_id = ? OR empresa_id IS NULL)" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params)
    puntos_venta = _rows_or_empty(conn, "SELECT id, nombre FROM puntos_venta WHERE (empresa_id = ? OR empresa_id IS NULL)" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params)
    conn.close()
    return render_template(
        'empresas/equipos_activos.html',
        activos=activos,
        resumen=resumen,
        familias=familias,
        familia=familia,
        estado=estado,
        q=q,
        dispositivos_sin_activo=dispositivos_sin_activo,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        puntos_venta=puntos_venta,
    )


@empresas_bp.route('/impresion/dispositivos', methods=['GET', 'POST'])
@login_required
@permiso_requerido('gestionar_empresas')
def dispositivos_impresion():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    ensure_etiquetas_schema(conn, empresa_id)
    device_uid = request.cookies.get(DEVICE_COOKIE) or uuid.uuid4().hex
    conn.commit()

    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'crear_dispositivo':
            nuevo_id = create_print_device_from_form(conn, empresa_id, request.form)
            if nuevo_id:
                conn.commit()
                flash('Dispositivo creado. Cuando se use fisicamente, copia su token en el agente local si hace falta.', 'success')
            else:
                flash('Indica al menos un nombre para crear el dispositivo.', 'warning')
        else:
            uid_form = device_uid if accion == 'guardar_actual' else None
            if update_print_device_from_form(conn, empresa_id, request.form, uid_form):
                conn.commit()
                flash('Dispositivo de impresion actualizado.', 'success')
            else:
                flash('No se pudo actualizar el dispositivo.', 'warning')
        conn.close()
        response = make_response(redirect(url_for('empresas.dispositivos_impresion')))
        response.set_cookie(DEVICE_COOKIE, device_uid, max_age=60 * 60 * 24 * 365 * 5, samesite='Lax')
        return response

    formatos = get_label_configs(conn, empresa_id, solo_activas=True)
    dispositivo_actual = get_current_print_device(conn, empresa_id, device_uid, create=False)
    dispositivos = get_print_devices(conn, empresa_id)
    cola_resumen = _resumen_cola_por_dispositivo(conn, empresa_id)
    centro_id = centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro_id else ""
    centro_params = [centro_id] if centro_id else []
    departamentos = _rows_or_empty(conn, "SELECT id, nombre FROM departamentos WHERE (empresa_id = ? OR empresa_id IS NULL)" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params)
    subdepartamentos = _rows_or_empty(conn, "SELECT id, nombre FROM subdepartamentos WHERE (empresa_id = ? OR empresa_id IS NULL)" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params)
    puntos_venta = _rows_or_empty(conn, "SELECT id, nombre FROM puntos_venta WHERE (empresa_id = ? OR empresa_id IS NULL)" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params)
    conn.commit()
    conn.close()

    response = make_response(render_template(
        'empresas/dispositivos_impresion.html',
        formatos=formatos,
        dispositivo_actual=dispositivo_actual,
        dispositivos=dispositivos,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        puntos_venta=puntos_venta,
        device_uid=device_uid,
        cola_resumen=cola_resumen,
    ))
    response.set_cookie(DEVICE_COOKIE, device_uid, max_age=60 * 60 * 24 * 365 * 5, samesite='Lax')
    return response


@empresas_bp.route('/impresion/dispositivos/<int:device_id>/probar', methods=['POST'])
@login_required
@permiso_requerido('gestionar_empresas')
def probar_dispositivo_impresion(device_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    ensure_print_queue_schema(conn, empresa_id)
    dispositivo = conn.execute(
        """
        SELECT *
        FROM config_dispositivos_impresion
        WHERE id = ? AND empresa_id = ?
        LIMIT 1
        """,
        (device_id, empresa_id),
    ).fetchone()
    if not dispositivo:
        conn.close()
        flash('Dispositivo no encontrado.', 'warning')
        return redirect(url_for('empresas.dispositivos_impresion'))

    config = get_label_config(conn, empresa_id, None, dispositivo['device_uid'])
    ahora = datetime.now().strftime('%d/%m/%Y %H:%M')
    rows = [
        ('Equipo', dispositivo['nombre']),
        ('Impresora', dispositivo['impresora_etiquetas_nombre'] or 'Predeterminada'),
        ('Formato', config.get('nombre') or config.get('codigo') or '-'),
        ('Fecha', ahora),
    ]
    contenido_raw = zpl_label('EL RACO ERP', 'PRUEBA OK', rows, config)
    contenido_html = f"""
    <!doctype html>
    <html lang="es">
    <head>
        <meta charset="utf-8">
        <title>Prueba impresora</title>
        <style>
            @page {{ size: {config.get('ancho_mm', 58)}mm {config.get('alto_mm', 40)}mm; margin: 0; }}
            body {{ font-family: Arial, sans-serif; margin:0; padding:4mm; color:#111; }}
            .box {{ border:2px solid #111; padding:4mm; min-height:{max(float(config.get('alto_mm', 40)) - 8, 20)}mm; box-sizing:border-box; }}
            h1 {{ font-size:16px; margin:0 0 4mm; }}
            p {{ font-size:11px; margin:1.5mm 0; }}
            strong {{ font-size:18px; }}
        </style>
    </head>
    <body>
        <div class="box">
            <h1>EL RACO ERP</h1>
            <strong>PRUEBA OK</strong>
            <p>Equipo: {dispositivo['nombre']}</p>
            <p>Impresora: {dispositivo['impresora_etiquetas_nombre'] or 'Predeterminada'}</p>
            <p>Fecha: {ahora}</p>
        </div>
    </body>
    </html>
    """
    tipo_impresora = (config.get('tipo_impresora') or '').lower()
    lenguaje = 'zpl' if tipo_impresora in {'zebra_zpl', 'termica'} else 'html'
    job_id = create_print_job(
        conn,
        empresa_id=empresa_id,
        device_uid=dispositivo['device_uid'],
        tipo='prueba_impresora',
        origen_tabla='config_dispositivos_impresion',
        origen_id=device_id,
        titulo=f"Prueba impresora {dispositivo['nombre']}",
        contenido_html=contenido_html,
        contenido_raw=contenido_raw,
        formato_codigo=config.get('codigo') or '',
        lenguaje_impresion=lenguaje,
    )
    conn.commit()
    conn.close()
    flash(f'Prueba enviada a la cola de impresion #{job_id}.', 'success')
    return redirect(url_for('empresas.dispositivos_impresion'))


@empresas_bp.route('/impresion/enviar', methods=['POST'])
@login_required
def crear_trabajo_impresion():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    device_uid = request.cookies.get(DEVICE_COOKIE) or uuid.uuid4().hex
    contenido_html = request.form.get('contenido_html') or ''
    next_url = request.form.get('next_url') or request.referrer or url_for('home')

    if not contenido_html.strip():
        conn.close()
        flash('No se pudo preparar el contenido de impresion.', 'warning')
        response = make_response(redirect(next_url))
        response.set_cookie(DEVICE_COOKIE, device_uid, max_age=60 * 60 * 24 * 365 * 5, samesite='Lax')
        return response

    job_id = create_print_job(
        conn,
        empresa_id=empresa_id,
        device_uid=device_uid,
        tipo=request.form.get('tipo') or 'etiqueta',
        origen_tabla=request.form.get('origen_tabla') or '',
        origen_id=int(request.form.get('origen_id') or 0),
        titulo=request.form.get('titulo') or 'Etiqueta',
        contenido_html=contenido_html,
        contenido_raw=request.form.get('contenido_raw') or '',
        contenido_texto=request.form.get('contenido_texto') or '',
        formato_codigo=request.form.get('formato_codigo') or '',
        lenguaje_impresion=request.form.get('lenguaje_impresion') or '',
    )
    conn.commit()
    dispositivo = get_current_print_device(conn, empresa_id, device_uid)
    conn.close()

    impresora = dispositivo.get('impresora_etiquetas_nombre') if dispositivo else ''
    if impresora and dispositivo and dispositivo.get('online'):
        flash(f'Trabajo #{job_id} enviado a cola para {impresora}.', 'success')
    elif impresora:
        flash(f'Trabajo #{job_id} en cola para {impresora}, pero el agente de este equipo esta offline.', 'warning')
    else:
        flash(f'Trabajo #{job_id} en cola. Falta asignar impresora a este equipo.', 'warning')
    response = make_response(redirect(next_url))
    response.set_cookie(DEVICE_COOKIE, device_uid, max_age=60 * 60 * 24 * 365 * 5, samesite='Lax')
    return response


@empresas_bp.route('/impresion/cola')
@login_required
@permiso_requerido('gestionar_empresas')
def cola_impresion():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    estado = request.args.get('estado') or ''
    ensure_print_queue_schema(conn, empresa_id)
    trabajos = list_print_jobs(conn, empresa_id, estado or None, limit=120)
    dispositivos = get_print_devices(conn, empresa_id)
    conn.close()
    return render_template(
        'empresas/cola_impresion.html',
        trabajos=trabajos,
        dispositivos=dispositivos,
        estado=estado,
    )


@empresas_bp.route('/impresion/cola/<int:job_id>/accion', methods=['POST'])
@login_required
@permiso_requerido('gestionar_empresas')
def accion_trabajo_impresion(job_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    ensure_print_queue_schema(conn, empresa_id)
    accion = (request.form.get('accion') or '').strip().lower()
    trabajo = conn.execute(
        "SELECT * FROM cola_impresion WHERE id = ? AND empresa_id = ?",
        (job_id, empresa_id),
    ).fetchone()
    if not trabajo:
        conn.close()
        flash('Trabajo de impresion no encontrado.', 'warning')
        return redirect(url_for('empresas.cola_impresion'))

    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if accion == 'reintentar':
        conn.execute(
            """
            UPDATE cola_impresion
            SET estado = 'pendiente', intentos = 0, error = '', processed_at = NULL, updated_at = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (ahora, job_id, empresa_id),
        )
        flash(f'Trabajo #{job_id} marcado para reintentar.', 'success')
    elif accion == 'cancelar':
        conn.execute(
            """
            UPDATE cola_impresion
            SET estado = 'cancelado', error = '', processed_at = ?, updated_at = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (ahora, ahora, job_id, empresa_id),
        )
        flash(f'Trabajo #{job_id} cancelado.', 'info')
    elif accion == 'marcar_impreso':
        conn.execute(
            """
            UPDATE cola_impresion
            SET estado = 'impreso', error = '', processed_at = ?, updated_at = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (ahora, ahora, job_id, empresa_id),
        )
        flash(f'Trabajo #{job_id} marcado como impreso.', 'success')
    else:
        flash('Accion no reconocida.', 'warning')
    conn.commit()
    conn.close()
    estado = request.form.get('estado_filtro') or ''
    return redirect(url_for('empresas.cola_impresion', estado=estado) if estado else url_for('empresas.cola_impresion'))


@empresas_bp.route('/impresion/api/trabajos')
def api_trabajos_impresion():
    conn = get_db()
    empresa_id = int(request.args.get('empresa_id') or 1)
    device_uid = request.args.get('device_uid') or ''
    token = request.args.get('token') or ''
    device = api_touch_device(
        conn,
        empresa_id,
        device_uid,
        token,
        request.args.get('version') or '',
        request.headers.get('X-Forwarded-For') or request.remote_addr or '',
    )
    if not device:
        conn.close()
        return jsonify({'ok': False, 'error': 'dispositivo_no_autorizado'}), 403
    conn.commit()
    trabajos = api_pending_jobs(conn, empresa_id, device_uid, limit=int(request.args.get('limit') or 5))
    payload = [dict(row) for row in trabajos]
    conn.close()
    return jsonify({'ok': True, 'jobs': payload})


@empresas_bp.route('/impresion/api/ping', methods=['GET', 'POST'])
def api_ping_impresion():
    conn = get_db()
    data = request.get_json(silent=True) or request.values
    empresa_id = int(data.get('empresa_id') or 1)
    device_uid = data.get('device_uid') or ''
    token = data.get('token') or ''
    version = data.get('version') or ''
    ip_ultima = request.headers.get('X-Forwarded-For') or request.remote_addr or ''
    device = api_touch_device(conn, empresa_id, device_uid, token, version, ip_ultima)
    if not device:
        conn.close()
        return jsonify({'ok': False, 'error': 'dispositivo_no_autorizado'}), 403
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@empresas_bp.route('/impresion/api/trabajos/<int:job_id>/contenido')
def api_contenido_trabajo_impresion(job_id):
    conn = get_db()
    empresa_id = int(request.args.get('empresa_id') or 1)
    device_uid = request.args.get('device_uid') or ''
    token = request.args.get('token') or ''
    device = api_get_device(conn, empresa_id, device_uid, token)
    if not device:
        conn.close()
        return jsonify({'ok': False, 'error': 'dispositivo_no_autorizado'}), 403
    job = get_job_for_device(conn, empresa_id, device_uid, job_id)
    if not job:
        conn.close()
        return jsonify({'ok': False, 'error': 'trabajo_no_encontrado'}), 404
    contenido = job['contenido_html'] or job['contenido_texto'] or ''
    conn.close()
    return Response(contenido, mimetype='text/html; charset=utf-8')


@empresas_bp.route('/impresion/api/trabajos/<int:job_id>/raw')
def api_raw_trabajo_impresion(job_id):
    conn = get_db()
    empresa_id = int(request.args.get('empresa_id') or 1)
    device_uid = request.args.get('device_uid') or ''
    token = request.args.get('token') or ''
    device = api_get_device(conn, empresa_id, device_uid, token)
    if not device:
        conn.close()
        return jsonify({'ok': False, 'error': 'dispositivo_no_autorizado'}), 403
    job = get_job_for_device(conn, empresa_id, device_uid, job_id)
    if not job:
        conn.close()
        return jsonify({'ok': False, 'error': 'trabajo_no_encontrado'}), 404
    contenido = job['contenido_raw'] or job['contenido_html'] or job['contenido_texto'] or ''
    conn.close()
    return Response(contenido, mimetype='text/plain; charset=utf-8')


@empresas_bp.route('/impresion/api/trabajos/<int:job_id>/estado', methods=['POST'])
def api_estado_trabajo_impresion(job_id):
    conn = get_db()
    data = request.get_json(silent=True) or request.form
    empresa_id = int(data.get('empresa_id') or request.args.get('empresa_id') or 1)
    device_uid = data.get('device_uid') or request.args.get('device_uid') or ''
    token = data.get('token') or request.args.get('token') or ''
    device = api_get_device(conn, empresa_id, device_uid, token)
    if not device:
        conn.close()
        return jsonify({'ok': False, 'error': 'dispositivo_no_autorizado'}), 403
    update_job_status(
        conn,
        empresa_id,
        device_uid,
        job_id,
        data.get('estado') or 'error',
        data.get('error') or '',
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ============================================
# NUEVA EMPRESA
# ============================================
@empresas_bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@permiso_requerido('gestionar_empresas')
def nueva_empresa():
    if request.method == "POST":
        nombre = request.form["nombre"]
        nif = request.form.get("nif", "")
        direccion = request.form.get("direccion", "")
        telefono = request.form.get("telefono", "")
        email = request.form.get("email", "")
        logo = _guardar_logo_empresa(request.files.get("logo"))
        
        conn = get_db()
        conn.execute("""
            INSERT INTO empresas (nombre, nif, direccion, telefono, email, logo)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (nombre, nif, direccion, telefono, email, logo))
        conn.commit()
        conn.close()
        
        flash(' Empresa creada', 'success')
        return redirect(url_for('empresas.listar_empresas'))
    
    return render_template('empresas/nueva_empresa.html')

# ============================================
# EDITAR EMPRESA
# ============================================
@empresas_bp.route('/<int:empresa_id>/editar', methods=['GET', 'POST'])
@login_required
@permiso_requerido('gestionar_empresas')
def editar_empresa(empresa_id):
    conn = get_db()
    
    if request.method == "POST":
        nombre = request.form["nombre"]
        nif = request.form.get("nif", "")
        direccion = request.form.get("direccion", "")
        telefono = request.form.get("telefono", "")
        email = request.form.get("email", "")
        activo = 1 if request.form.get("activo") else 0
        logo_actual = request.form.get("logo_actual") or ""
        logo_nuevo = _guardar_logo_empresa(request.files.get("logo"), empresa_id)
        logo = logo_nuevo or logo_actual
        
        conn.execute("""
            UPDATE empresas 
            SET nombre=?, nif=?, direccion=?, telefono=?, email=?, logo=?, activo=?
            WHERE id=?
        """, (nombre, nif, direccion, telefono, email, logo, activo, empresa_id))
        conn.commit()
        conn.close()
        
        flash(' Empresa actualizada', 'success')
        return redirect(url_for('empresas.listar_empresas'))
    
    empresa = conn.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    conn.close()
    return render_template('empresas/editar_empresa.html', empresa=empresa)


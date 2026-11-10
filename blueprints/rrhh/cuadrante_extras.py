from datetime import date, timedelta

from flask import Blueprint, flash, redirect, request, url_for

from database import get_db
from . import rrhh_bp
from blueprints.auth.routes import login_required

from .routes import (
    _empresa_id,
    _to_int,
    _ensure_editable_schema,
    _require_rrhh_global,
    _registrar_historial,
    _safe_update,
)

# NOTA: usamos el blueprint rrhh_bp ya existente. El endpoint real será rrhh.<función>.
# Este archivo se importa después de routes.py desde blueprints/rrhh/__init__.py.


def _turno_solapa(conn, empleado_id, fecha, hora_inicio, hora_fin, excluir_id=None):
    sql = """
        SELECT id FROM turnos_rrhh
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND date(fecha) = date(?)
          AND COALESCE(estado, 'planificado') != 'anulado'
          AND COALESCE(hora_inicio, '') = COALESCE(?, '')
          AND COALESCE(hora_fin, '') = COALESCE(?, '')
    """
    params = [_empresa_id(), empleado_id, fecha, hora_inicio, hora_fin]
    if excluir_id:
        sql += " AND id != ?"
        params.append(excluir_id)
    sql += " LIMIT 1"
    return conn.execute(sql, params).fetchone()


def _anular_turnos_activos_dia(conn, empleado_id, fecha):
    filas = conn.execute(
        """
        SELECT id FROM turnos_rrhh
        WHERE empresa_id = ? AND empleado_id = ? AND date(fecha) = date(?)
          AND COALESCE(estado, 'planificado') != 'anulado'
        """,
        (_empresa_id(), empleado_id, fecha),
    ).fetchall()
    for fila in filas:
        _safe_update(conn, 'turnos_rrhh', fila['id'], {'estado': 'anulado'}, 'Sobrescrito al copiar horario/libre')
    return len(filas)


@rrhh_bp.route('/cuadrantes/real/turno/<int:id>/copiar-semana', methods=['POST'])
@login_required
def copiar_turno_semana(id):
    # El decorador login_required ya no se importa aquí para evitar conflictos; protegemos con _require_rrhh_global.
    return _copiar_turno_semana_impl(id)


@_require_rrhh_global
def _copiar_turno_semana_impl(id):
    conn = get_db()
    _ensure_editable_schema(conn)
    empresa_id = _empresa_id()
    turno = conn.execute("SELECT * FROM turnos_rrhh WHERE id = ? AND empresa_id = ?", (id, empresa_id)).fetchone()
    if not turno:
        conn.close()
        flash('Turno origen no encontrado.', 'warning')
        return redirect(url_for('rrhh.cuadrante_real'))

    try:
        fecha_origen = date.fromisoformat(turno['fecha'])
    except Exception:
        fecha_origen = date.today()
    inicio_semana = fecha_origen - timedelta(days=fecha_origen.weekday())

    modo = request.form.get('modo') or 'semana'
    estrategia = request.form.get('estrategia') or 'vacios'
    if modo == 'laborables':
        dias_indices = [0, 1, 2, 3, 4]
    elif modo == 'seleccionados':
        dias_indices = [_to_int(d, -1) for d in request.form.getlist('dias_destino')]
        dias_indices = [d for d in dias_indices if 0 <= d <= 6]
    else:
        dias_indices = [0, 1, 2, 3, 4, 5, 6]

    creados = 0
    omitidos = 0
    anulados_previos = 0
    for idx in dias_indices:
        fecha_destino = inicio_semana + timedelta(days=idx)
        if fecha_destino == fecha_origen:
            continue

        existentes_dia = conn.execute(
            """
            SELECT id FROM turnos_rrhh
            WHERE empresa_id = ? AND empleado_id = ? AND date(fecha) = date(?)
              AND COALESCE(estado, 'planificado') != 'anulado'
            """,
            (empresa_id, turno['empleado_id'], fecha_destino.isoformat()),
        ).fetchall()

        if estrategia == 'vacios' and existentes_dia:
            omitidos += 1
            continue
        if estrategia == 'sobrescribir' and existentes_dia:
            anulados_previos += _anular_turnos_activos_dia(conn, turno['empleado_id'], fecha_destino.isoformat())
        elif estrategia == 'duplicar' and _turno_solapa(conn, turno['empleado_id'], fecha_destino.isoformat(), turno['hora_inicio'], turno['hora_fin']):
            omitidos += 1
            continue

        conn.execute(
            """
            INSERT INTO turnos_rrhh
            (empleado_id, fecha, hora_inicio, hora_fin, tipo, centro_id, departamento_id, subdepartamento_id,
             puesto_id, estado, origen, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turno['empleado_id'], fecha_destino.isoformat(), turno['hora_inicio'], turno['hora_fin'],
                turno['tipo'] or 'real', turno['centro_id'] if 'centro_id' in turno.keys() else None,
                turno['departamento_id'] if 'departamento_id' in turno.keys() else None,
                turno['subdepartamento_id'] if 'subdepartamento_id' in turno.keys() else None,
                turno['puesto_id'] if 'puesto_id' in turno.keys() else None,
                turno['estado'] if 'estado' in turno.keys() and turno['estado'] else 'planificado',
                'copiado_semana', turno['observaciones'] if 'observaciones' in turno.keys() else '', empresa_id,
            ),
        )
        nuevo_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        _registrar_historial(conn, 'turnos_rrhh', nuevo_id, 'copiar_semana', f'Copiado desde turno {id}')
        creados += 1

    conn.commit()
    conn.close()
    flash(f'Horario copiado: {creados} creados, {anulados_previos} anteriores anulados, {omitidos} omitidos.', 'success')
    return redirect(url_for(
        'rrhh.cuadrante_real',
        fecha=fecha_origen.isoformat(),
        centro_id=request.form.get('filtro_centro_id') or '',
        departamento_id=request.form.get('filtro_departamento_id') or '',
        subdepartamento_id=request.form.get('filtro_subdepartamento_id') or '',
        puesto_id=request.form.get('filtro_puesto_id') or '',
    ))


@rrhh_bp.route('/cuadrantes/real/libre', methods=['POST'])
@login_required
def marcar_dia_libre_cuadrante_real():
    return _marcar_dia_libre_cuadrante_real_impl()


@_require_rrhh_global
def _marcar_dia_libre_cuadrante_real_impl():
    conn = get_db()
    _ensure_editable_schema(conn)
    empresa_id = _empresa_id()
    empleado_id = request.form.get('empleado_id')
    fecha = request.form.get('fecha')
    if not empleado_id or not fecha:
        conn.close()
        flash('Empleado y fecha son obligatorios para marcar día libre.', 'warning')
        return redirect(url_for('rrhh.cuadrante_real'))

    estrategia = request.form.get('estrategia') or 'vacios'
    existentes = conn.execute(
        """
        SELECT id FROM turnos_rrhh
        WHERE empresa_id = ? AND empleado_id = ? AND date(fecha) = date(?)
          AND COALESCE(estado, 'planificado') != 'anulado'
        """,
        (empresa_id, empleado_id, fecha),
    ).fetchall()
    if existentes and estrategia == 'vacios':
        conn.close()
        flash('Ese día ya tiene turno. No se ha marcado libre porque elegiste solo rellenar vacíos.', 'warning')
        return redirect(url_for('rrhh.cuadrante_real', fecha=fecha))
    if existentes and estrategia == 'sobrescribir':
        _anular_turnos_activos_dia(conn, empleado_id, fecha)

    conn.execute(
        """
        INSERT INTO turnos_rrhh
        (empleado_id, fecha, hora_inicio, hora_fin, tipo, centro_id, departamento_id, subdepartamento_id,
         puesto_id, estado, origen, observaciones, empresa_id)
        VALUES (?, ?, NULL, NULL, 'libre', ?, ?, ?, NULL, 'libre', 'cuadrante_real', ?, ?)
        """,
        (
            empleado_id, fecha,
            request.form.get('centro_id') or None,
            request.form.get('departamento_id') or None,
            request.form.get('subdepartamento_id') or None,
            request.form.get('observaciones') or 'Día libre / descanso',
            empresa_id,
        ),
    )
    nuevo_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
    _registrar_historial(conn, 'turnos_rrhh', nuevo_id, 'marcar_libre', 'Día libre registrado desde cuadrante')
    conn.commit()
    conn.close()
    flash('Día libre registrado en el cuadrante.', 'success')
    return redirect(url_for(
        'rrhh.cuadrante_real',
        fecha=fecha,
        centro_id=request.form.get('filtro_centro_id') or request.form.get('centro_id') or '',
        departamento_id=request.form.get('filtro_departamento_id') or request.form.get('departamento_id') or '',
        subdepartamento_id=request.form.get('filtro_subdepartamento_id') or request.form.get('subdepartamento_id') or '',
        puesto_id=request.form.get('filtro_puesto_id') or '',
    ))


@rrhh_bp.route('/cuadrantes/real/libre/copiar-semana', methods=['POST'])
@login_required
def copiar_libre_semana_cuadrante_real():
    return _copiar_libre_semana_cuadrante_real_impl()


@_require_rrhh_global
def _copiar_libre_semana_cuadrante_real_impl():
    conn = get_db()
    _ensure_editable_schema(conn)
    empresa_id = _empresa_id()
    empleado_id = request.form.get('empleado_id')
    fecha = request.form.get('fecha')
    try:
        fecha_base = date.fromisoformat(fecha)
    except Exception:
        fecha_base = date.today()
    inicio_semana = fecha_base - timedelta(days=fecha_base.weekday())
    dias_indices = [_to_int(d, -1) for d in request.form.getlist('dias_destino')]
    dias_indices = [d for d in dias_indices if 0 <= d <= 6]
    if not dias_indices:
        dias_indices = [fecha_base.weekday()]
    estrategia = request.form.get('estrategia') or 'vacios'
    creados = 0
    omitidos = 0
    for idx in dias_indices:
        fecha_destino = (inicio_semana + timedelta(days=idx)).isoformat()
        existentes = conn.execute(
            """
            SELECT id FROM turnos_rrhh
            WHERE empresa_id = ? AND empleado_id = ? AND date(fecha) = date(?)
              AND COALESCE(estado, 'planificado') != 'anulado'
            """,
            (empresa_id, empleado_id, fecha_destino),
        ).fetchall()
        if existentes and estrategia == 'vacios':
            omitidos += 1
            continue
        if existentes and estrategia == 'sobrescribir':
            _anular_turnos_activos_dia(conn, empleado_id, fecha_destino)
        conn.execute(
            """
            INSERT INTO turnos_rrhh
            (empleado_id, fecha, hora_inicio, hora_fin, tipo, centro_id, departamento_id, subdepartamento_id,
             puesto_id, estado, origen, observaciones, empresa_id)
            VALUES (?, ?, NULL, NULL, 'libre', ?, ?, ?, NULL, 'libre', 'copiar_libre_semana', ?, ?)
            """,
            (
                empleado_id, fecha_destino, request.form.get('centro_id') or None,
                request.form.get('departamento_id') or None, request.form.get('subdepartamento_id') or None,
                request.form.get('observaciones') or 'Día libre / descanso', empresa_id,
            ),
        )
        nuevo_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        _registrar_historial(conn, 'turnos_rrhh', nuevo_id, 'copiar_libre_semana', 'Libre copiado en semana')
        creados += 1
    conn.commit()
    conn.close()
    flash(f'Días libres registrados: {creados}. Omitidos: {omitidos}.', 'success')
    return redirect(url_for(
        'rrhh.cuadrante_real',
        fecha=fecha_base.isoformat(),
        centro_id=request.form.get('filtro_centro_id') or request.form.get('centro_id') or '',
        departamento_id=request.form.get('filtro_departamento_id') or request.form.get('departamento_id') or '',
        subdepartamento_id=request.form.get('filtro_subdepartamento_id') or request.form.get('subdepartamento_id') or '',
        puesto_id=request.form.get('filtro_puesto_id') or '',
    ))

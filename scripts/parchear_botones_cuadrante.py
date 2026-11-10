from pathlib import Path

ruta = Path('blueprints/rrhh/routes.py')
texto = ruta.read_text(encoding='utf-8')

extra = '''

# ============================================================
# CUADRANTE REAL — botones copiar horario y marcar libre
# ============================================================
def _rrhh_redirect_cuadrante_real_desde_form(fecha=None):
    return redirect(url_for(
        "rrhh.cuadrante_real",
        fecha=request.form.get("filtro_fecha") or fecha or request.form.get("fecha") or date.today().isoformat(),
        centro_id=request.form.get("filtro_centro_id") or request.form.get("centro_id") or "",
        departamento_id=request.form.get("filtro_departamento_id") or request.form.get("departamento_id") or "",
        subdepartamento_id=request.form.get("filtro_subdepartamento_id") or request.form.get("subdepartamento_id") or "",
        puesto_id=request.form.get("filtro_puesto_id") or request.form.get("puesto_id") or "",
    ))


def _rrhh_turno_activo_existe(conn, empleado_id, fecha):
    return conn.execute(
        """
        SELECT id FROM turnos_rrhh
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND date(fecha) = date(?)
          AND COALESCE(estado, 'planificado') NOT IN ('anulado', 'cancelado')
        LIMIT 1
        """,
        (_empresa_id(), empleado_id, fecha),
    ).fetchone()


@rrhh_bp.route('/cuadrantes/real/turno/<int:id>/copiar', methods=['POST'])
@login_required
@_require_rrhh_global
def copiar_turno_cuadrante_real(id):
    conn = get_db()
    _ensure_editable_schema(conn)
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()

    origen = conn.execute("SELECT * FROM turnos_rrhh WHERE id = ? AND empresa_id = ?", (id, empresa_id)).fetchone()
    if not origen:
        conn.close()
        flash('Turno origen no encontrado.', 'warning')
        return _rrhh_redirect_cuadrante_real_desde_form()

    try:
        fecha_origen = date.fromisoformat(origen['fecha'])
    except Exception:
        fecha_origen = date.today()

    inicio_semana = fecha_origen - timedelta(days=fecha_origen.weekday())
    modo = request.form.get('modo_copia') or 'semana'
    estrategia = request.form.get('estrategia') or 'vacios'

    if modo == 'lunes_viernes':
        dias = [0, 1, 2, 3, 4]
    elif modo == 'finde':
        dias = [5, 6]
    elif modo == 'dias':
        dias = [_to_int(d, -1) for d in request.form.getlist('dias_destino')]
        dias = [d for d in dias if 0 <= d <= 6]
    else:
        dias = [0, 1, 2, 3, 4, 5, 6]

    creados = 0
    omitidos = 0
    anulados = 0
    for dia_idx in dias:
        fecha_destino = inicio_semana + timedelta(days=dia_idx)
        if fecha_destino == fecha_origen:
            continue

        existente = _rrhh_turno_activo_existe(conn, origen['empleado_id'], fecha_destino.isoformat())
        if existente and estrategia != 'sobrescribir':
            omitidos += 1
            continue
        if existente and estrategia == 'sobrescribir':
            _safe_update(conn, 'turnos_rrhh', existente['id'], {'estado': 'anulado'}, 'Sobrescrito al copiar horario')
            anulados += 1

        conn.execute(
            """
            INSERT INTO turnos_rrhh
            (empleado_id, fecha, hora_inicio, hora_fin, tipo, centro_id, departamento_id, subdepartamento_id, puesto_id, estado, origen, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                origen['empleado_id'], fecha_destino.isoformat(), origen['hora_inicio'], origen['hora_fin'],
                origen['tipo'] or 'real', origen['centro_id'] if 'centro_id' in origen.keys() else None,
                origen['departamento_id'] if 'departamento_id' in origen.keys() else None,
                origen['subdepartamento_id'] if 'subdepartamento_id' in origen.keys() else None,
                origen['puesto_id'] if 'puesto_id' in origen.keys() else None,
                origen['estado'] or 'planificado', 'copia_cuadrante', origen['observaciones'], empresa_id,
            ),
        )
        nuevo_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        _registrar_historial(conn, 'turnos_rrhh', nuevo_id, 'copiar_turno', f'Copiado desde turno {id}')
        creados += 1

    conn.commit()
    conn.close()
    flash(f'Copiado: {creados} creados, {omitidos} omitidos, {anulados} sobrescritos/anulados.', 'success')
    return _rrhh_redirect_cuadrante_real_desde_form(fecha_origen.isoformat())


@rrhh_bp.route('/cuadrantes/real/marcar-libre', methods=['POST'])
@login_required
@_require_rrhh_global
def marcar_libre_cuadrante_real():
    conn = get_db()
    _ensure_editable_schema(conn)
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    empleado_id = request.form.get('empleado_id')
    fecha = request.form.get('fecha')
    if not empleado_id or not fecha:
        conn.close()
        flash('Empleado y fecha son obligatorios para marcar libre.', 'warning')
        return _rrhh_redirect_cuadrante_real_desde_form(fecha)

    existente = _rrhh_turno_activo_existe(conn, empleado_id, fecha)
    if existente:
        _safe_update(conn, 'turnos_rrhh', existente['id'], {'estado': 'anulado'}, 'Sustituido por día libre')

    conn.execute(
        """
        INSERT INTO turnos_rrhh
        (empleado_id, fecha, hora_inicio, hora_fin, tipo, centro_id, departamento_id, subdepartamento_id, puesto_id, estado, origen, observaciones, empresa_id)
        VALUES (?, ?, NULL, NULL, 'libre', ?, ?, ?, ?, 'libre', 'cuadrante_real', 'Día libre / descanso', ?)
        """,
        (
            empleado_id, fecha, request.form.get('centro_id') or None,
            request.form.get('departamento_id') or None, request.form.get('subdepartamento_id') or None,
            request.form.get('puesto_id') or None, empresa_id,
        ),
    )
    nuevo_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
    _registrar_historial(conn, 'turnos_rrhh', nuevo_id, 'marcar_libre', 'Día libre registrado desde cuadrante')
    conn.commit()
    conn.close()
    flash('Día libre registrado en el cuadrante.', 'success')
    return _rrhh_redirect_cuadrante_real_desde_form(fecha)
'''

if 'def copiar_turno_cuadrante_real' not in texto:
    texto = texto.rstrip() + "\n" + extra + "\n"
    ruta.write_text(texto, encoding='utf-8')
    print('OK: rutas añadidas')
else:
    print('OK: las rutas ya existían, no se ha duplicado nada')

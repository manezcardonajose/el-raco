from pathlib import Path
import re
import shutil

ROOT = Path.cwd()
routes_path = ROOT / 'blueprints' / 'rrhh' / 'routes.py'
template_src = Path(__file__).resolve().parents[1] / 'templates' / 'rrhh' / 'cuadrante_real.html'
template_dst = ROOT / 'templates' / 'rrhh' / 'cuadrante_real.html'

if not routes_path.exists():
    raise SystemExit(f'No encuentro {routes_path}. Ejecuta este script desde la raíz del proyecto pepe4.')
if not template_src.exists():
    raise SystemExit(f'No encuentro plantilla origen {template_src}')

template_dst.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(template_src, template_dst)

text = routes_path.read_text(encoding='utf-8')

# Asegurar import date/timedelta si hiciera falta
if 'from datetime import' in text and 'timedelta' not in text.split('from datetime import',1)[1].split('\n',1)[0]:
    text = re.sub(r'from datetime import ([^\n]+)', lambda m: 'from datetime import ' + m.group(1).rstrip() + ', timedelta', text, count=1)

# Reemplaza la función asignar_cuadrante_real para que siempre vuelva al cuadrante real con los filtros.
asignar_func = r'''
@rrhh_bp.route("/cuadrantes/real/asignar", methods=["POST"])
@login_required
@_require_rrhh_global
def asignar_cuadrante_real():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    fecha = request.form.get("fecha")
    empleado_id = request.form.get("empleado_id")
    hora_inicio = request.form.get("hora_inicio")
    hora_fin = request.form.get("hora_fin")
    centro_id = request.form.get("centro_id") or None
    departamento_id = request.form.get("departamento_id") or None
    subdepartamento_id = request.form.get("subdepartamento_id") or None
    puesto_id = request.form.get("puesto_id") or None

    if not empleado_id or not fecha or not hora_inicio or not hora_fin:
        conn.close()
        flash("Empleado, fecha, hora inicio y hora fin son obligatorios.", "warning")
        return redirect(url_for(
            "rrhh.cuadrante_real",
            fecha=fecha or date.today().isoformat(),
            centro_id=request.form.get("filtro_centro_id") or centro_id or "",
            departamento_id=request.form.get("filtro_departamento_id") or departamento_id or "",
            subdepartamento_id=request.form.get("filtro_subdepartamento_id") or subdepartamento_id or "",
            puesto_id=request.form.get("filtro_puesto_id") or puesto_id or "",
        ))

    conn.execute(
        """
        INSERT INTO turnos_rrhh
        (empleado_id, fecha, hora_inicio, hora_fin, tipo, centro_id, departamento_id, subdepartamento_id, puesto_id, estado, origen, observaciones, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            empleado_id, fecha, hora_inicio, hora_fin, request.form.get("tipo") or "real",
            centro_id, departamento_id, subdepartamento_id, puesto_id,
            request.form.get("estado") or "planificado", "cuadrante_real", request.form.get("observaciones"), empresa_id,
        ),
    )
    conn.commit()
    conn.close()
    flash("Turno asignado al cuadrante real.", "success")
    return redirect(url_for(
        "rrhh.cuadrante_real",
        fecha=fecha,
        centro_id=request.form.get("filtro_centro_id") or centro_id or "",
        departamento_id=request.form.get("filtro_departamento_id") or departamento_id or "",
        subdepartamento_id=request.form.get("filtro_subdepartamento_id") or subdepartamento_id or "",
        puesto_id=request.form.get("filtro_puesto_id") or puesto_id or "",
    ))
'''
pattern = re.compile(r'@rrhh_bp\.route\(["\']/cuadrantes/real/asignar["\'][\s\S]*?(?=\n# ============================================================|\n@rrhh_bp\.route|\Z)')
if pattern.search(text):
    text = pattern.sub(asignar_func.strip() + '\n', text, count=1)
else:
    text += '\n\n' + asignar_func.strip() + '\n'

extras = r'''

# ============================================================
# FIX FINAL CUADRANTE REAL — copiar horarios, marcar libres y volver siempre a cuadrante real
# ============================================================

def _rrhh_redirect_cuadrante_real(fecha=None):
    return redirect(url_for(
        'rrhh.cuadrante_real',
        fecha=fecha or request.form.get('fecha') or date.today().isoformat(),
        centro_id=request.form.get('filtro_centro_id') or request.form.get('centro_id') or '',
        departamento_id=request.form.get('filtro_departamento_id') or request.form.get('departamento_id') or '',
        subdepartamento_id=request.form.get('filtro_subdepartamento_id') or request.form.get('subdepartamento_id') or '',
        puesto_id=request.form.get('filtro_puesto_id') or request.form.get('puesto_id') or '',
    ))


def _turno_activo_where():
    return "COALESCE(estado, 'planificado') NOT IN ('anulado', 'cancelado')"


def _anular_turnos_activos_dia_final(conn, empleado_id, fecha):
    filas = conn.execute(
        f"""
        SELECT id FROM turnos_rrhh
        WHERE empresa_id = ? AND empleado_id = ? AND date(fecha) = date(?)
          AND {_turno_activo_where()}
        """,
        (_empresa_id(), empleado_id, fecha),
    ).fetchall()
    total = 0
    for fila in filas:
        try:
            _safe_update(conn, 'turnos_rrhh', fila['id'], {'estado': 'anulado'}, 'Anulado por sobrescritura de cuadrante')
        except Exception:
            conn.execute("UPDATE turnos_rrhh SET estado='anulado' WHERE id=?", (fila['id'],))
        total += 1
    return total


def _turno_solapa_final(conn, empleado_id, fecha, hora_inicio, hora_fin):
    if not hora_inicio or not hora_fin:
        return False
    row = conn.execute(
        f"""
        SELECT id FROM turnos_rrhh
        WHERE empresa_id = ? AND empleado_id = ? AND date(fecha) = date(?)
          AND {_turno_activo_where()}
          AND COALESCE(tipo,'') != 'libre'
          AND hora_inicio = ? AND hora_fin = ?
        LIMIT 1
        """,
        (_empresa_id(), empleado_id, fecha, hora_inicio, hora_fin),
    ).fetchone()
    return row is not None


if 'copiar_turno_semana' not in globals():
    @rrhh_bp.route('/cuadrantes/real/turno/<int:id>/copiar-semana', methods=['POST'])
    @login_required
    @_require_rrhh_global
    def copiar_turno_semana(id):
        conn = get_db()
        _ensure_editable_schema(conn)
        turno = conn.execute('SELECT * FROM turnos_rrhh WHERE id = ? AND empresa_id = ?', (id, _empresa_id())).fetchone()
        if not turno:
            conn.close()
            flash('Turno origen no encontrado.', 'warning')
            return _rrhh_redirect_cuadrante_real()
        if (turno['tipo'] if 'tipo' in turno.keys() else '') == 'libre' or (turno['estado'] if 'estado' in turno.keys() else '') == 'libre':
            conn.close()
            flash('Para copiar libres usa el botón Copiar libre.', 'warning')
            return _rrhh_redirect_cuadrante_real(turno['fecha'])

        fecha_origen = date.fromisoformat(turno['fecha'])
        inicio_semana = fecha_origen - timedelta(days=fecha_origen.weekday())
        modo = request.form.get('modo') or 'semana'
        if modo == 'laborables':
            dias_indices = [0, 1, 2, 3, 4]
        elif modo == 'seleccionados':
            dias_indices = [_to_int(d, -1) for d in request.form.getlist('dias_destino')]
            dias_indices = [d for d in dias_indices if 0 <= d <= 6]
        else:
            dias_indices = [0, 1, 2, 3, 4, 5, 6]
        estrategia = request.form.get('estrategia') or 'vacios'
        creados = omitidos = anulados_previos = 0
        for idx in dias_indices:
            fecha_destino = inicio_semana + timedelta(days=idx)
            if fecha_destino == fecha_origen:
                continue
            existentes = conn.execute(
                f"""
                SELECT id FROM turnos_rrhh
                WHERE empresa_id = ? AND empleado_id = ? AND date(fecha) = date(?)
                  AND {_turno_activo_where()}
                """,
                (_empresa_id(), turno['empleado_id'], fecha_destino.isoformat()),
            ).fetchall()
            if estrategia == 'vacios' and existentes:
                omitidos += 1
                continue
            if estrategia == 'sobrescribir' and existentes:
                anulados_previos += _anular_turnos_activos_dia_final(conn, turno['empleado_id'], fecha_destino.isoformat())
            elif estrategia == 'duplicar' and _turno_solapa_final(conn, turno['empleado_id'], fecha_destino.isoformat(), turno['hora_inicio'], turno['hora_fin']):
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
                    turno['tipo'] if 'tipo' in turno.keys() and turno['tipo'] else 'real',
                    turno['centro_id'] if 'centro_id' in turno.keys() else None,
                    turno['departamento_id'] if 'departamento_id' in turno.keys() else None,
                    turno['subdepartamento_id'] if 'subdepartamento_id' in turno.keys() else None,
                    turno['puesto_id'] if 'puesto_id' in turno.keys() else None,
                    'planificado', 'copiado_semana', turno['observaciones'] if 'observaciones' in turno.keys() else '', _empresa_id(),
                ),
            )
            try:
                nuevo_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
                _registrar_historial(conn, 'turnos_rrhh', nuevo_id, 'copiar_semana', f'Copiado desde turno {id}')
            except Exception:
                pass
            creados += 1
        conn.commit()
        conn.close()
        flash(f'Horario copiado: {creados} creados, {anulados_previos} anteriores anulados, {omitidos} omitidos.', 'success')
        return _rrhh_redirect_cuadrante_real(fecha_origen.isoformat())


if 'marcar_dia_libre_cuadrante_real' not in globals():
    @rrhh_bp.route('/cuadrantes/real/libre', methods=['POST'])
    @login_required
    @_require_rrhh_global
    def marcar_dia_libre_cuadrante_real():
        conn = get_db()
        _ensure_editable_schema(conn)
        empleado_id = request.form.get('empleado_id')
        fecha = request.form.get('fecha')
        if not empleado_id or not fecha:
            conn.close()
            flash('Empleado y fecha son obligatorios para marcar libre.', 'warning')
            return _rrhh_redirect_cuadrante_real()
        existentes = conn.execute(
            f"""
            SELECT id FROM turnos_rrhh
            WHERE empresa_id = ? AND empleado_id = ? AND date(fecha) = date(?)
              AND {_turno_activo_where()}
            """,
            (_empresa_id(), empleado_id, fecha),
        ).fetchall()
        estrategia = request.form.get('estrategia') or 'vacios'
        if existentes and estrategia == 'vacios':
            conn.close()
            flash('Ese día ya tiene turno. No se marca libre salvo que sobrescribas.', 'warning')
            return _rrhh_redirect_cuadrante_real(fecha)
        if existentes and estrategia == 'sobrescribir':
            _anular_turnos_activos_dia_final(conn, empleado_id, fecha)
        conn.execute(
            """
            INSERT INTO turnos_rrhh
            (empleado_id, fecha, hora_inicio, hora_fin, tipo, centro_id, departamento_id, subdepartamento_id,
             puesto_id, estado, origen, observaciones, empresa_id)
            VALUES (?, ?, NULL, NULL, 'libre', ?, ?, ?, NULL, 'libre', 'cuadrante_real', ?, ?)
            """,
            (
                empleado_id, fecha, request.form.get('centro_id') or None,
                request.form.get('departamento_id') or None, request.form.get('subdepartamento_id') or None,
                request.form.get('observaciones') or 'Día libre / descanso', _empresa_id(),
            ),
        )
        try:
            nuevo_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
            _registrar_historial(conn, 'turnos_rrhh', nuevo_id, 'marcar_libre', 'Día libre registrado desde cuadrante')
        except Exception:
            pass
        conn.commit()
        conn.close()
        flash('Día libre registrado en el cuadrante.', 'success')
        return _rrhh_redirect_cuadrante_real(fecha)


if 'copiar_libre_semana_cuadrante_real' not in globals():
    @rrhh_bp.route('/cuadrantes/real/libre/copiar-semana', methods=['POST'])
    @login_required
    @_require_rrhh_global
    def copiar_libre_semana_cuadrante_real():
        conn = get_db()
        _ensure_editable_schema(conn)
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
            dias_indices = [0, 1, 2, 3, 4, 5, 6]
        estrategia = request.form.get('estrategia') or 'vacios'
        creados = omitidos = 0
        for idx in dias_indices:
            fecha_destino = (inicio_semana + timedelta(days=idx)).isoformat()
            existentes = conn.execute(
                f"""
                SELECT id FROM turnos_rrhh
                WHERE empresa_id = ? AND empleado_id = ? AND date(fecha) = date(?)
                  AND {_turno_activo_where()}
                """,
                (_empresa_id(), empleado_id, fecha_destino),
            ).fetchall()
            if existentes and estrategia == 'vacios':
                omitidos += 1
                continue
            if existentes and estrategia == 'sobrescribir':
                _anular_turnos_activos_dia_final(conn, empleado_id, fecha_destino)
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
                    request.form.get('observaciones') or 'Día libre / descanso', _empresa_id(),
                ),
            )
            creados += 1
        conn.commit()
        conn.close()
        flash(f'Días libres registrados: {creados}. Omitidos: {omitidos}.', 'success')
        return _rrhh_redirect_cuadrante_real(fecha_base.isoformat())
'''

# If the endpoints are missing, append. If they exist, keep existing definitions but template will still work.
if 'FIX FINAL CUADRANTE REAL' not in text:
    text += extras

routes_path.write_text(text, encoding='utf-8')
print('OK: plantilla cuadrante_real.html sustituida y routes.py parcheado.')

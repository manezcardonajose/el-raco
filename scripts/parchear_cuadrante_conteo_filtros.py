from pathlib import Path
import re

ROOT = Path.cwd()
ROUTES = ROOT / 'blueprints' / 'rrhh' / 'routes.py'
TPL = ROOT / 'templates' / 'rrhh' / 'cuadrante_real.html'

if not ROUTES.exists():
    raise SystemExit(f'No encuentro {ROUTES}. Ejecuta este script desde la raíz del proyecto pepe4.')
if not TPL.exists():
    raise SystemExit(f'No encuentro {TPL}. Ejecuta este script desde la raíz del proyecto pepe4.')

routes = ROUTES.read_text(encoding='utf-8')

def replace_function(text, decorator_pattern, func_name, new_code):
    # Busca desde el decorador inmediatamente anterior hasta el siguiente decorador @rrhh_bp.route o fin de archivo.
    pattern = re.compile(
        rf"{decorator_pattern}\s*\n@login_required(?:\s*\n@_require_rrhh_global)?\s*\ndef {func_name}\([^)]*\):.*?(?=\n@rrhh_bp\.route|\Z)",
        re.S,
    )
    m = pattern.search(text)
    if not m:
        raise SystemExit(f'No he podido localizar la función {func_name} en routes.py')
    return text[:m.start()] + new_code.rstrip() + '\n\n' + text[m.end():]

new_cuadrante_real = r'''@rrhh_bp.route("/cuadrantes/real")
@login_required
def cuadrante_real():
    """
    Cuadrante real corregido:
    - Los filtros deciden qué trabajadores se muestran.
    - Los turnos se cargan por trabajador + semana.
    - Los turnos anulados/libres no cuentan como cobertura.
    - La cobertura se calcula por necesidad individual, no por clave genérica compartida.
    - Un trabajador solo cuenta una vez por necesidad y día.
    - Si una necesidad tiene puesto, solo cuenta ese puesto; si el turno no trae puesto,
      puede contar por categoría equivalente del empleado.
    """
    conn = get_db()
    _ensure_cuadrantes_schema(conn)

    empresa_id = _empresa_id()
    inicio, fin = _rango_semana(request.args.get("fecha"))

    centro_id = request.args.get("centro_id") or ""
    departamento_id = request.args.get("departamento_id") or ""
    subdepartamento_id = request.args.get("subdepartamento_id") or ""
    puesto_id = request.args.get("puesto_id") or ""

    # ============================================================
    # TRABAJADORES FILTRADOS
    # ============================================================
    filtros = ["e.empresa_id = ?", "COALESCE(e.activo,1)=1"]
    params = [empresa_id]

    if departamento_id:
        filtros.append("e.departamento_id = ?")
        params.append(departamento_id)

    if subdepartamento_id:
        filtros.append("e.subdepartamento_id = ?")
        params.append(subdepartamento_id)

    if not _usuario_es_global_rrhh() and _session_departamento_id():
        filtros.append("e.departamento_id = ?")
        params.append(_session_departamento_id())

    empleados = conn.execute(
        f"""
        SELECT
            e.id,
            e.nombre,
            e.apellidos,
            e.departamento_id,
            e.subdepartamento_id,
            e.categoria_id,
            d.nombre AS departamento_nombre,
            sd.nombre AS subdepartamento_nombre,
            c.nombre AS categoria_nombre
        FROM empleados e
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
        LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
        WHERE {' AND '.join(filtros)}
        ORDER BY COALESCE(d.nombre,''), COALESCE(sd.nombre,''), e.nombre, e.apellidos
        """,
        params,
    ).fetchall()

    empleado_ids = [e["id"] for e in empleados]

    # ============================================================
    # TURNOS DE LA SEMANA
    # ============================================================
    turnos = []

    if empleado_ids:
        placeholders = ",".join(["?"] * len(empleado_ids))
        turnos = conn.execute(
            f"""
            SELECT
                t.*,
                TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) AS empleado_nombre,

                e.departamento_id AS empleado_departamento_id,
                e.subdepartamento_id AS empleado_subdepartamento_id,
                e.categoria_id AS empleado_categoria_id,

                COALESCE(t.departamento_id, e.departamento_id) AS departamento_efectivo_id,
                COALESCE(t.subdepartamento_id, e.subdepartamento_id) AS subdepartamento_efectivo_id,

                ct.nombre AS centro_nombre,
                COALESCE(d_t.nombre, d_e.nombre) AS departamento_nombre,
                COALESCE(sd_t.nombre, sd_e.nombre) AS subdepartamento_nombre,

                p.nombre AS puesto_nombre,
                p.categoria_id AS puesto_categoria_id
            FROM turnos_rrhh t
            JOIN empleados e ON t.empleado_id = e.id
            LEFT JOIN centros_trabajo ct ON t.centro_id = ct.id
            LEFT JOIN departamentos d_t ON t.departamento_id = d_t.id
            LEFT JOIN departamentos d_e ON e.departamento_id = d_e.id
            LEFT JOIN subdepartamentos sd_t ON t.subdepartamento_id = sd_t.id
            LEFT JOIN subdepartamentos sd_e ON e.subdepartamento_id = sd_e.id
            LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
            WHERE t.empresa_id = ?
              AND t.empleado_id IN ({placeholders})
              AND date(t.fecha) BETWEEN date(?) AND date(?)
              AND COALESCE(t.estado, 'planificado') NOT IN ('anulado', 'cancelado', 'borrado')
            ORDER BY date(t.fecha), e.nombre, t.hora_inicio
            """,
            [empresa_id] + empleado_ids + [inicio.isoformat(), fin.isoformat()],
        ).fetchall()

    # ============================================================
    # VACACIONES / AUSENCIAS
    # ============================================================
    vacaciones = []

    if empleado_ids:
        placeholders = ",".join(["?"] * len(empleado_ids))
        vacaciones = conn.execute(
            f"""
            SELECT
                v.*,
                TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) AS empleado_nombre
            FROM vacaciones_rrhh v
            JOIN empleados e ON v.empleado_id = e.id
            WHERE v.empresa_id = ?
              AND v.empleado_id IN ({placeholders})
              AND date(v.fecha_inicio) <= date(?)
              AND date(v.fecha_fin) >= date(?)
              AND v.estado IN ('solicitada','aprobada')
            ORDER BY v.fecha_inicio
            """,
            [empresa_id] + empleado_ids + [fin.isoformat(), inicio.isoformat()],
        ).fetchall()

    # ============================================================
    # NECESIDADES OPERATIVAS
    # ============================================================
    necesidades_params = [empresa_id]
    necesidades_where = ["n.empresa_id = ?", "COALESCE(n.activo,1)=1"]

    if centro_id:
        necesidades_where.append("n.centro_id = ?")
        necesidades_params.append(centro_id)
    if departamento_id:
        necesidades_where.append("n.departamento_id = ?")
        necesidades_params.append(departamento_id)
    if subdepartamento_id:
        necesidades_where.append("n.subdepartamento_id = ?")
        necesidades_params.append(subdepartamento_id)
    if puesto_id:
        necesidades_where.append("n.puesto_id = ?")
        necesidades_params.append(puesto_id)

    necesidades = conn.execute(
        f"""
        SELECT
            n.*,
            ct.nombre AS centro_nombre,
            d.nombre AS departamento_nombre,
            sd.nombre AS subdepartamento_nombre,
            p.nombre AS puesto_nombre,
            p.categoria_id AS puesto_categoria_id
        FROM necesidades_operativas n
        LEFT JOIN centros_trabajo ct ON n.centro_id = ct.id
        LEFT JOIN departamentos d ON n.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON n.subdepartamento_id = sd.id
        LEFT JOIN puestos_operativos p ON n.puesto_id = p.id
        WHERE {' AND '.join(necesidades_where)}
        ORDER BY n.dia_semana, COALESCE(ct.nombre,''), COALESCE(d.nombre,''), COALESCE(sd.nombre,''), COALESCE(p.nombre,'')
        """,
        necesidades_params,
    ).fetchall()

    # ============================================================
    # LISTAS PARA FILTROS
    # ============================================================
    centros = conn.execute(
        """
        SELECT id, nombre
        FROM centros_trabajo
        WHERE empresa_id = ? AND COALESCE(activo,1)=1
        ORDER BY nombre
        """,
        (empresa_id,),
    ).fetchall()

    departamentos = conn.execute(
        """
        SELECT id, nombre
        FROM departamentos
        WHERE empresa_id = ?
        ORDER BY nombre
        """,
        (empresa_id,),
    ).fetchall()

    subdepartamentos = conn.execute(
        """
        SELECT id, nombre, departamento_id
        FROM subdepartamentos
        WHERE empresa_id = ?
        ORDER BY nombre
        """,
        (empresa_id,),
    ).fetchall()

    puestos = conn.execute(
        """
        SELECT id, nombre, centro_id, departamento_id, subdepartamento_id, categoria_id
        FROM puestos_operativos
        WHERE empresa_id = ? AND COALESCE(activo,1)=1
        ORDER BY nombre
        """,
        (empresa_id,),
    ).fetchall()

    conn.close()

    dias = [inicio + timedelta(days=i) for i in range(7)]

    # ============================================================
    # TURNOS POR EMPLEADO / DÍA
    # ============================================================
    turnos_por_empleado_dia = {}
    for t in turnos:
        turno = dict(t)
        turnos_por_empleado_dia.setdefault((turno["empleado_id"], turno["fecha"]), []).append(turno)

    # ============================================================
    # VACACIONES POR EMPLEADO / DÍA
    # ============================================================
    vacaciones_por_empleado_dia = {}
    for v in vacaciones:
        try:
            vi = date.fromisoformat(v["fecha_inicio"])
            vf = date.fromisoformat(v["fecha_fin"])
        except Exception:
            continue
        for dia in dias:
            if vi <= dia <= vf:
                vacaciones_por_empleado_dia.setdefault((v["empleado_id"], dia.isoformat()), []).append(dict(v))

    # ============================================================
    # COBERTURA REAL — CONTEO POR NECESIDAD INDIVIDUAL
    # ============================================================
    cobertura = {}
    asignados = {}
    asignados_necesidad = {}

    turnos_contables = []
    for t in turnos:
        td = dict(t)
        estado = (td.get("estado") or "planificado").strip().lower()
        tipo = (td.get("tipo") or "real").strip().lower()

        if estado in {"anulado", "cancelado", "borrado"}:
            continue
        if tipo in {"libre", "descanso", "dia_libre", "día libre"}:
            continue

        try:
            td["_weekday"] = date.fromisoformat(td["fecha"]).weekday()
        except Exception:
            continue

        td["_centro_id"] = td.get("centro_id") or 0
        td["_departamento_id"] = td.get("departamento_efectivo_id") or td.get("departamento_id") or td.get("empleado_departamento_id") or 0
        td["_subdepartamento_id"] = td.get("subdepartamento_efectivo_id") or td.get("subdepartamento_id") or td.get("empleado_subdepartamento_id") or 0
        td["_puesto_id"] = td.get("puesto_id") or 0
        td["_empleado_categoria_id"] = td.get("empleado_categoria_id") or 0
        turnos_contables.append(td)

    def _mismo(a, b):
        return str(a or 0) == str(b or 0)

    for n in necesidades:
        nd = dict(n)
        necesidad_id = nd.get("id")
        dia = nd.get("dia_semana")
        centro = nd.get("centro_id") or 0
        depto = nd.get("departamento_id") or 0
        subdepto = nd.get("subdepartamento_id") or 0
        puesto = nd.get("puesto_id") or 0
        puesto_categoria = nd.get("puesto_categoria_id") or 0
        personas_necesarias = nd.get("personas_necesarias") or 0

        key = (dia, puesto, depto, subdepto, centro, necesidad_id)
        cobertura[key] = personas_necesarias

        empleados_contados = set()

        for t in turnos_contables:
            if t["_weekday"] != dia:
                continue

            empleado_turno = t.get("empleado_id")
            if empleado_turno in empleados_contados:
                continue

            if centro and not _mismo(t["_centro_id"], centro):
                continue
            if subdepto:
                if not _mismo(t["_subdepartamento_id"], subdepto):
                    continue
            elif depto:
                if not _mismo(t["_departamento_id"], depto):
                    continue

            if puesto:
                if _mismo(t["_puesto_id"], puesto):
                    empleados_contados.add(empleado_turno)
                elif not t["_puesto_id"] and puesto_categoria and _mismo(t["_empleado_categoria_id"], puesto_categoria):
                    empleados_contados.add(empleado_turno)
                else:
                    continue
            else:
                empleados_contados.add(empleado_turno)

        total = len(empleados_contados)
        asignados_necesidad[necesidad_id] = total
        asignados[key] = total

    return render_template(
        "rrhh/cuadrante_real.html",
        empleados=empleados,
        dias=dias,
        inicio=inicio,
        fin=fin,
        centros=centros,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        puestos=puestos,
        turnos_por_empleado_dia=turnos_por_empleado_dia,
        vacaciones_por_empleado_dia=vacaciones_por_empleado_dia,
        necesidades=necesidades,
        cobertura=cobertura,
        asignados=asignados,
        asignados_necesidad=asignados_necesidad,
        centro_id=centro_id,
        departamento_id=departamento_id,
        subdepartamento_id=subdepartamento_id,
        puesto_id=puesto_id,
        puede_editar=_usuario_es_global_rrhh(),
        timedelta=timedelta,
    )
'''

new_anular_turno = r'''@rrhh_bp.route('/turnos/<int:id>/anular', methods=['POST'])
@login_required
@_require_rrhh_global
def anular_turno_rrhh(id):
    conn = get_db()
    _ensure_editable_schema(conn)

    turno = _get_row(conn, 'turnos_rrhh', id)

    fecha = request.form.get('fecha')
    if not fecha and turno:
        fecha = turno['fecha']
    if not fecha:
        fecha = date.today().isoformat()

    centro_id = request.form.get('centro_id') or request.form.get('filtro_centro_id') or ''
    departamento_id = request.form.get('departamento_id') or request.form.get('filtro_departamento_id') or ''
    subdepartamento_id = request.form.get('subdepartamento_id') or request.form.get('filtro_subdepartamento_id') or ''
    puesto_id = request.form.get('puesto_id') or request.form.get('filtro_puesto_id') or ''

    motivo = request.form.get('motivo_cambio') or 'Turno anulado desde cuadrante real'

    if _column_exists(conn, 'turnos_rrhh', 'estado'):
        _safe_update(conn, 'turnos_rrhh', id, {'estado': 'anulado'}, motivo)
    else:
        conn.execute(
            'DELETE FROM turnos_rrhh WHERE id = ? AND empresa_id = ?',
            (id, _empresa_id()),
        )

    conn.commit()
    conn.close()

    flash('Turno anulado.', 'success')

    return redirect(url_for(
        'rrhh.cuadrante_real',
        fecha=fecha,
        centro_id=centro_id,
        departamento_id=departamento_id,
        subdepartamento_id=subdepartamento_id,
        puesto_id=puesto_id,
    ))
'''

routes = replace_function(routes, r'@rrhh_bp\.route\("/cuadrantes/real"\)', 'cuadrante_real', new_cuadrante_real)
routes = replace_function(routes, r"@rrhh_bp\.route\('/turnos/<int:id>/anular', methods=\['POST'\]\)", 'anular_turno_rrhh', new_anular_turno)
ROUTES.write_text(routes, encoding='utf-8')
print('OK routes.py parcheado')

# Parche template: cobertura individual y filtros en anular
tpl = TPL.read_text(encoding='utf-8')

# Usar asignados_necesidad si existe
replacements = [
    ("{% set cubiertos = asignados.get(k, 0) %}", "{% set cubiertos = asignados_necesidad.get(n.id, asignados.get(k, 0)) %}"),
    ("{% set cubiertos = asignados.get(k,0) %}", "{% set cubiertos = asignados_necesidad.get(n.id, asignados.get(k, 0)) %}"),
]
for old, new in replacements:
    tpl = tpl.replace(old, new)

# Añadir hidden fields a formularios de anular turno si faltan
pattern = re.compile(r"(<form[^>]+url_for\('rrhh\.anular_turno_rrhh', id=t\.id\)[^>]*>)", re.S)

def add_hidden(match):
    form_tag = match.group(1)
    # Si ya tiene fecha justo después no duplicamos
    start = match.end()
    following = tpl[start:start+500]
    if 'name="fecha"' in following and 'name="departamento_id"' in following:
        return form_tag
    hidden = '''
                    <input type="hidden" name="fecha" value="{{ inicio.isoformat() }}">
                    <input type="hidden" name="centro_id" value="{{ centro_id }}">
                    <input type="hidden" name="departamento_id" value="{{ departamento_id }}">
                    <input type="hidden" name="subdepartamento_id" value="{{ subdepartamento_id }}">
                    <input type="hidden" name="puesto_id" value="{{ puesto_id }}">
                    <input type="hidden" name="motivo_cambio" value="Turno anulado desde cuadrante real">'''
    return form_tag + hidden

tpl = pattern.sub(add_hidden, tpl)
TPL.write_text(tpl, encoding='utf-8')
print('OK cuadrante_real.html parcheado')

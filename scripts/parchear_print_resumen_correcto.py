from pathlib import Path
from datetime import datetime

ROOT = Path.cwd()
routes_path = ROOT / 'blueprints' / 'rrhh' / 'routes.py'
backup_path = routes_path.with_suffix('.py.bak_print_resumen_' + datetime.now().strftime('%Y%m%d_%H%M%S'))

if not routes_path.exists():
    raise SystemExit(f'No encuentro {routes_path}. Ejecuta este script desde la raíz del proyecto pepe4.')

text = routes_path.read_text(encoding='utf-8')

start_marker = '@rrhh_bp.route("/cuadrantes/real/imprimir")'
start = text.find(start_marker)
if start == -1:
    start_marker = "@rrhh_bp.route('/cuadrantes/real/imprimir')"
    start = text.find(start_marker)
if start == -1:
    raise SystemExit('No encuentro la ruta /cuadrantes/real/imprimir en routes.py')

next_route = text.find('\n@rrhh_bp.route', start + len(start_marker))
if next_route == -1:
    end = len(text)
else:
    end = next_route

new_func = r'''@rrhh_bp.route("/cuadrantes/real/imprimir")
@login_required
def imprimir_cuadrante_real():
    """
    Impresión profesional del cuadrante real.

    Reglas:
    - Respeta filtros activos: semana, centro, departamento, subdepartamento y puesto.
    - Carga trabajadores por ficha y también correturnos con turno dentro del filtro.
    - Carga horarios reales.
    - El resumen cuenta PERSONAS operativas únicas por día y categoría laboral del trabajador.
    - No cuenta libres, descansos, anulados, cancelados ni borrados.
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
    # 1. TRABAJADORES BASE SEGÚN FILTROS DE FICHA
    # ============================================================
    filtros_empleados = ["e.empresa_id = ?", "COALESCE(e.activo,1)=1"]
    params_empleados = [empresa_id]

    if departamento_id:
        filtros_empleados.append("e.departamento_id = ?")
        params_empleados.append(departamento_id)

    if subdepartamento_id:
        filtros_empleados.append("e.subdepartamento_id = ?")
        params_empleados.append(subdepartamento_id)

    if not _usuario_es_global_rrhh() and _session_departamento_id():
        filtros_empleados.append("e.departamento_id = ?")
        params_empleados.append(_session_departamento_id())

    empleados_base = conn.execute(
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
        WHERE {' AND '.join(filtros_empleados)}
        ORDER BY COALESCE(d.nombre,''), COALESCE(sd.nombre,''), e.nombre, e.apellidos
        """,
        params_empleados,
    ).fetchall()

    empleado_ids_base = [e["id"] for e in empleados_base]

    # ============================================================
    # 2. TURNOS REALES DE LA SEMANA SEGÚN FILTROS OPERATIVOS
    # ============================================================
    turnos_where = [
        "t.empresa_id = ?",
        "date(t.fecha) BETWEEN date(?) AND date(?)",
        "COALESCE(t.estado, 'planificado') NOT IN ('anulado', 'cancelado', 'borrado')",
    ]
    turnos_params = [empresa_id, inicio.isoformat(), fin.isoformat()]

    if centro_id:
        turnos_where.append("COALESCE(t.centro_id, 0) = ?")
        turnos_params.append(centro_id)

    if departamento_id:
        turnos_where.append("COALESCE(t.departamento_id, e.departamento_id) = ?")
        turnos_params.append(departamento_id)

    if subdepartamento_id:
        turnos_where.append("COALESCE(t.subdepartamento_id, e.subdepartamento_id) = ?")
        turnos_params.append(subdepartamento_id)

    if puesto_id:
        turnos_where.append("COALESCE(t.puesto_id, 0) = ?")
        turnos_params.append(puesto_id)

    if not _usuario_es_global_rrhh() and _session_departamento_id():
        turnos_where.append("COALESCE(t.departamento_id, e.departamento_id) = ?")
        turnos_params.append(_session_departamento_id())

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
            p.categoria_id AS puesto_categoria_id,
            c.nombre AS categoria_nombre
        FROM turnos_rrhh t
        JOIN empleados e ON t.empleado_id = e.id
        LEFT JOIN centros_trabajo ct ON t.centro_id = ct.id
        LEFT JOIN departamentos d_t ON t.departamento_id = d_t.id
        LEFT JOIN departamentos d_e ON e.departamento_id = d_e.id
        LEFT JOIN subdepartamentos sd_t ON t.subdepartamento_id = sd_t.id
        LEFT JOIN subdepartamentos sd_e ON e.subdepartamento_id = sd_e.id
        LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
        LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
        WHERE {' AND '.join(turnos_where)}
        ORDER BY date(t.fecha), e.nombre, t.hora_inicio
        """,
        turnos_params,
    ).fetchall()

    empleado_ids_turnos = sorted({t["empleado_id"] for t in turnos})
    empleado_ids_final = sorted(set(empleado_ids_base) | set(empleado_ids_turnos))

    # ============================================================
    # 3. TRABAJADORES FINALES: base + correturnos con turno filtrado
    # ============================================================
    empleados = []

    if empleado_ids_final:
        placeholders = ",".join(["?"] * len(empleado_ids_final))
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
            WHERE e.empresa_id = ?
              AND e.id IN ({placeholders})
            ORDER BY COALESCE(d.nombre,''), COALESCE(sd.nombre,''), e.nombre, e.apellidos
            """,
            [empresa_id] + empleado_ids_final,
        ).fetchall()

    # ============================================================
    # 4. VACACIONES / AUSENCIAS
    # ============================================================
    vacaciones = []

    if empleado_ids_final:
        placeholders = ",".join(["?"] * len(empleado_ids_final))
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
            [empresa_id] + empleado_ids_final + [fin.isoformat(), inicio.isoformat()],
        ).fetchall()

    # ============================================================
    # 5. NOMBRES DE FILTROS
    # ============================================================
    def _nombre(tabla, registro_id):
        if not registro_id:
            return ""
        try:
            row = conn.execute(
                f"SELECT nombre FROM {tabla} WHERE id = ? AND empresa_id = ?",
                (registro_id, empresa_id),
            ).fetchone()
            return row["nombre"] if row else ""
        except Exception:
            return ""

    filtro_centro = _nombre("centros_trabajo", centro_id)
    filtro_departamento = _nombre("departamentos", departamento_id)
    filtro_subdepartamento = _nombre("subdepartamentos", subdepartamento_id)
    filtro_puesto = _nombre("puestos_operativos", puesto_id)

    conn.close()

    dias = [inicio + timedelta(days=i) for i in range(7)]

    # ============================================================
    # 6. TURNOS POR EMPLEADO / DÍA
    # ============================================================
    turnos_por_empleado_dia = {}

    for t in turnos:
        turno = dict(t)
        turnos_por_empleado_dia.setdefault(
            (turno["empleado_id"], turno["fecha"]),
            [],
        ).append(turno)

    # ============================================================
    # 7. VACACIONES POR EMPLEADO / DÍA
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
                vacaciones_por_empleado_dia.setdefault(
                    (v["empleado_id"], dia.isoformat()),
                    [],
                ).append(dict(v))

    # ============================================================
    # 8. RESUMEN OPERATIVO REAL POR CATEGORÍA LABORAL DEL TRABAJADOR
    # ============================================================
    resumen_sets = {}
    total_dia_sets = {dia.isoformat(): set() for dia in dias}

    for t in turnos:
        turno = dict(t)
        estado = (turno.get("estado") or "planificado").strip().lower()
        tipo = (turno.get("tipo") or "real").strip().lower()

        if estado in {"anulado", "cancelado", "borrado"}:
            continue

        if tipo in {"libre", "descanso", "dia_libre", "día libre"}:
            continue

        fecha = turno.get("fecha")
        if fecha not in total_dia_sets:
            continue

        categoria = (turno.get("categoria_nombre") or "Sin categoría").strip()
        empleado_id = turno.get("empleado_id")

        resumen_sets.setdefault(categoria, {}).setdefault(fecha, set()).add(empleado_id)
        total_dia_sets[fecha].add(empleado_id)

    resumen_categorias = []

    for categoria in sorted(resumen_sets.keys(), key=lambda x: x.lower()):
        fila = {
            "categoria": categoria,
            "dias": {},
            "total_semana": 0,
        }

        empleados_semana = set()

        for dia in dias:
            fecha = dia.isoformat()
            empleados_dia = resumen_sets.get(categoria, {}).get(fecha, set())
            fila["dias"][fecha] = len(empleados_dia)
            empleados_semana.update(empleados_dia)

        fila["total_semana"] = len(empleados_semana)
        resumen_categorias.append(fila)

    total_por_dia = {}

    for dia in dias:
        fecha = dia.isoformat()
        total_por_dia[fecha] = len(total_dia_sets.get(fecha, set()))

    total_semana_operativos = len(set().union(*total_dia_sets.values())) if total_dia_sets else 0

    return render_template(
        "rrhh/cuadrante_real_print.html",
        empleados=empleados,
        dias=dias,
        inicio=inicio,
        fin=fin,
        turnos_por_empleado_dia=turnos_por_empleado_dia,
        vacaciones_por_empleado_dia=vacaciones_por_empleado_dia,
        filtro_centro=filtro_centro,
        filtro_departamento=filtro_departamento,
        filtro_subdepartamento=filtro_subdepartamento,
        filtro_puesto=filtro_puesto,
        resumen_categorias=resumen_categorias,
        total_por_dia=total_por_dia,
        total_semana_operativos=total_semana_operativos,
        generado_en=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )
'''

routes_path.write_text(text[:start] + new_func + text[end:], encoding='utf-8')
backup_path.write_text(text, encoding='utf-8')
print(f'OK. routes.py parcheado. Copia de seguridad: {backup_path}')

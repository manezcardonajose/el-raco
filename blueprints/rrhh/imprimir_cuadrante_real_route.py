@rrhh_bp.route("/cuadrantes/real/imprimir")
@login_required
def imprimir_cuadrante_real():
    """
    Vista de impresión del cuadrante real.
    Importante: carga turnos por trabajadores filtrados + semana, igual que cuadrante_real,
    para que no salgan celdas vacías cuando el turno no tiene centro/departamento guardado.
    """
    conn = get_db()
    _ensure_cuadrantes_schema(conn)

    empresa_id = _empresa_id()
    inicio, fin = _rango_semana(request.args.get("fecha"))

    centro_id = request.args.get("centro_id") or ""
    departamento_id = request.args.get("departamento_id") or ""
    subdepartamento_id = request.args.get("subdepartamento_id") or ""
    puesto_id = request.args.get("puesto_id") or ""

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

    filtro_centro = None
    if centro_id:
        row = conn.execute("SELECT nombre FROM centros_trabajo WHERE id = ? AND empresa_id = ?", (centro_id, empresa_id)).fetchone()
        filtro_centro = row["nombre"] if row else None

    filtro_departamento = None
    if departamento_id:
        row = conn.execute("SELECT nombre FROM departamentos WHERE id = ? AND empresa_id = ?", (departamento_id, empresa_id)).fetchone()
        filtro_departamento = row["nombre"] if row else None

    filtro_subdepartamento = None
    if subdepartamento_id:
        row = conn.execute("SELECT nombre FROM subdepartamentos WHERE id = ? AND empresa_id = ?", (subdepartamento_id, empresa_id)).fetchone()
        filtro_subdepartamento = row["nombre"] if row else None

    filtro_puesto = None
    if puesto_id:
        row = conn.execute("SELECT nombre FROM puestos_operativos WHERE id = ? AND empresa_id = ?", (puesto_id, empresa_id)).fetchone()
        filtro_puesto = row["nombre"] if row else None

    conn.close()

    dias = [inicio + timedelta(days=i) for i in range(7)]

    turnos_por_empleado_dia = {}
    for t in turnos:
        turno = dict(t)
        turnos_por_empleado_dia.setdefault((turno["empleado_id"], turno["fecha"]), []).append(turno)

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
        generado_en=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "blueprints" / "rrhh" / "routes.py"
TEMPLATE = ROOT / "templates" / "rrhh" / "cuadrante_real.html"
PRINT_TEMPLATE_SRC = ROOT / "templates" / "rrhh" / "cuadrante_real_print.html"

route_code = r'''

# ============================================================
# IMPRESIÓN PROFESIONAL CUADRANTE REAL
# ============================================================
@rrhh_bp.route("/cuadrantes/real/imprimir")
@login_required
def imprimir_cuadrante_real():
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
        SELECT e.id, e.nombre, e.apellidos, e.departamento_id, e.subdepartamento_id,
               d.nombre departamento_nombre, sd.nombre subdepartamento_nombre, c.nombre categoria_nombre
        FROM empleados e
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
        LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
        WHERE {' AND '.join(filtros)}
        ORDER BY COALESCE(d.nombre,''), COALESCE(sd.nombre,''), e.nombre, e.apellidos
        """,
        params,
    ).fetchall()

    turnos_params = [empresa_id, inicio.isoformat(), fin.isoformat()]
    turnos_where = ["t.empresa_id = ?", "date(t.fecha) BETWEEN date(?) AND date(?)", "COALESCE(t.estado,'planificado') NOT IN ('anulado','cancelado')"]
    if centro_id:
        turnos_where.append("COALESCE(t.centro_id, 0) = COALESCE(?, COALESCE(t.centro_id, 0))")
        turnos_params.append(centro_id)
    if departamento_id:
        turnos_where.append("COALESCE(t.departamento_id, e.departamento_id) = ?")
        turnos_params.append(departamento_id)
    if subdepartamento_id:
        turnos_where.append("COALESCE(t.subdepartamento_id, e.subdepartamento_id) = ?")
        turnos_params.append(subdepartamento_id)
    if puesto_id:
        turnos_where.append("t.puesto_id = ?")
        turnos_params.append(puesto_id)

    turnos = conn.execute(
        f"""
        SELECT t.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) empleado_nombre,
               ct.nombre centro_nombre, d.nombre departamento_nombre, sd.nombre subdepartamento_nombre, p.nombre puesto_nombre
        FROM turnos_rrhh t
        JOIN empleados e ON t.empleado_id = e.id
        LEFT JOIN centros_trabajo ct ON t.centro_id = ct.id
        LEFT JOIN departamentos d ON COALESCE(t.departamento_id, e.departamento_id) = d.id
        LEFT JOIN subdepartamentos sd ON COALESCE(t.subdepartamento_id, e.subdepartamento_id) = sd.id
        LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
        WHERE {' AND '.join(turnos_where)}
        ORDER BY date(t.fecha), e.nombre, t.hora_inicio
        """,
        turnos_params,
    ).fetchall()

    vacaciones = conn.execute(
        """
        SELECT v.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) empleado_nombre
        FROM vacaciones_rrhh v
        JOIN empleados e ON v.empleado_id = e.id
        WHERE v.empresa_id = ?
          AND date(v.fecha_inicio) <= date(?) AND date(v.fecha_fin) >= date(?)
          AND v.estado IN ('solicitada','aprobada')
        ORDER BY v.fecha_inicio
        """,
        (empresa_id, fin.isoformat(), inicio.isoformat()),
    ).fetchall()

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
        SELECT n.*, ct.nombre centro_nombre, d.nombre departamento_nombre, sd.nombre subdepartamento_nombre, p.nombre puesto_nombre
        FROM necesidades_operativas n
        LEFT JOIN centros_trabajo ct ON n.centro_id = ct.id
        LEFT JOIN departamentos d ON n.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON n.subdepartamento_id = sd.id
        LEFT JOIN puestos_operativos p ON n.puesto_id = p.id
        WHERE {' AND '.join(necesidades_where)}
        ORDER BY n.dia_semana, p.nombre
        """,
        necesidades_params,
    ).fetchall()

    filtro_centro = conn.execute("SELECT nombre FROM centros_trabajo WHERE id = ?", (centro_id,)).fetchone()["nombre"] if centro_id else None
    filtro_departamento = conn.execute("SELECT nombre FROM departamentos WHERE id = ?", (departamento_id,)).fetchone()["nombre"] if departamento_id else None
    filtro_subdepartamento = conn.execute("SELECT nombre FROM subdepartamentos WHERE id = ?", (subdepartamento_id,)).fetchone()["nombre"] if subdepartamento_id else None
    filtro_puesto = conn.execute("SELECT nombre FROM puestos_operativos WHERE id = ?", (puesto_id,)).fetchone()["nombre"] if puesto_id else None
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

    asignados = {}
    for t in turnos:
        key = (date.fromisoformat(t["fecha"]).weekday(), t["puesto_id"] or 0, t["departamento_id"] or 0, t["subdepartamento_id"] or 0)
        asignados[key] = asignados.get(key, 0) + 1

    return render_template(
        "rrhh/cuadrante_real_print.html",
        empleados=empleados,
        dias=dias,
        inicio=inicio,
        fin=fin,
        turnos_por_empleado_dia=turnos_por_empleado_dia,
        vacaciones_por_empleado_dia=vacaciones_por_empleado_dia,
        necesidades=necesidades,
        asignados=asignados,
        filtro_centro=filtro_centro,
        filtro_departamento=filtro_departamento,
        filtro_subdepartamento=filtro_subdepartamento,
        filtro_puesto=filtro_puesto,
        generado_en=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )
'''

def main():
    if not ROUTES.exists():
        raise SystemExit(f"No encuentro {ROUTES}")
    text = ROUTES.read_text(encoding="utf-8")
    if "def imprimir_cuadrante_real" not in text:
        ROUTES.write_text(text.rstrip() + route_code + "\n", encoding="utf-8")
        print("Ruta de impresión profesional añadida a routes.py")
    else:
        print("La ruta de impresión ya existe; no se duplica.")

    if not TEMPLATE.exists():
        raise SystemExit(f"No encuentro {TEMPLATE}")
    html = TEMPLATE.read_text(encoding="utf-8")
    old = '<button type="button" class="btn btn-info no-print" onclick="window.print()"><i class="fas fa-print"></i> Imprimir cuadrante</button>'
    new = '<a class="btn btn-info no-print" target="_blank" href="{{ url_for(\'rrhh.imprimir_cuadrante_real\', fecha=inicio.isoformat(), centro_id=centro_id, departamento_id=departamento_id, subdepartamento_id=subdepartamento_id, puesto_id=puesto_id) }}"><i class="fas fa-print"></i> Imprimir profesional</a>'
    if old in html:
        html = html.replace(old, new)
    elif "rrhh.imprimir_cuadrante_real" not in html:
        marker = '<a class="btn btn-info" href="{{ url_for(\'rrhh.lista_puestos_operativos\') }}"><i class="fas fa-briefcase"></i> Puestos</a>'
        html = html.replace(marker, marker + "\n      " + new)
    TEMPLATE.write_text(html, encoding="utf-8")
    print("Botón de impresión profesional actualizado en cuadrante_real.html")

if __name__ == "__main__":
    main()

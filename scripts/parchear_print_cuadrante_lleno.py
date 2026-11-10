from pathlib import Path
import re
import shutil

ROOT = Path.cwd()
routes_path = ROOT / "blueprints" / "rrhh" / "routes.py"
template_src = Path(__file__).resolve().parents[1] / "templates" / "rrhh" / "cuadrante_real_print.html"
template_dst = ROOT / "templates" / "rrhh" / "cuadrante_real_print.html"

if not routes_path.exists():
    raise SystemExit(f"No encuentro routes.py en {routes_path}")

backup = routes_path.with_suffix(".py.bak_print_cuadrante")
shutil.copy2(routes_path, backup)

funcion = r'''
# ============================================================
# IMPRESIÓN PROFESIONAL CUADRANTE REAL
# ============================================================
@rrhh_bp.route("/cuadrantes/real/imprimir")
@login_required
def imprimir_cuadrante_real():
    """
    Impresión del cuadrante real.

    Carga datos de forma robusta:
    - primero trabajadores según filtros de ficha,
    - luego turnos de la semana según filtros operativos,
    - une ambos para que no quede vacío si hay correturnos,
    - respeta departamento, subdepartamento y puesto,
    - no cuenta ni imprime anulados/cancelados/borrados,
    - muestra resumen operativo por categoría/puesto y día.
    """
    conn = get_db()
    _ensure_cuadrantes_schema(conn)

    empresa_id = _empresa_id()
    inicio, fin = _rango_semana(request.args.get("fecha"))

    centro_id = request.args.get("centro_id") or ""
    departamento_id = request.args.get("departamento_id") or ""
    subdepartamento_id = request.args.get("subdepartamento_id") or ""
    puesto_id = request.args.get("puesto_id") or ""

    # ------------------------------------------------------------
    # 1) Empleados base por filtros de ficha
    # ------------------------------------------------------------
    filtros_emp = ["e.empresa_id = ?", "COALESCE(e.activo,1)=1"]
    params_emp = [empresa_id]

    if departamento_id:
        filtros_emp.append("e.departamento_id = ?")
        params_emp.append(departamento_id)

    if subdepartamento_id:
        filtros_emp.append("e.subdepartamento_id = ?")
        params_emp.append(subdepartamento_id)

    if not _usuario_es_global_rrhh() and _session_departamento_id():
        filtros_emp.append("e.departamento_id = ?")
        params_emp.append(_session_departamento_id())

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
        WHERE {' AND '.join(filtros_emp)}
        ORDER BY COALESCE(d.nombre,''), COALESCE(sd.nombre,''), e.nombre, e.apellidos
        """,
        params_emp,
    ).fetchall()

    empleados_base_ids = [e["id"] for e in empleados_base]

    # ------------------------------------------------------------
    # 2) Turnos de la semana por filtros operativos
    # ------------------------------------------------------------
    where_turnos = [
        "t.empresa_id = ?",
        "date(t.fecha) BETWEEN date(?) AND date(?)",
        "COALESCE(t.estado, 'planificado') NOT IN ('anulado', 'cancelado', 'borrado')",
    ]
    params_turnos = [empresa_id, inicio.isoformat(), fin.isoformat()]

    # Si hay empleados base, los incluimos para que la impresión no se vacíe.
    # Además dejamos que entren correturnos por los filtros operativos de turno.
    bloques_or = []

    if empleados_base_ids:
        placeholders = ",".join(["?"] * len(empleados_base_ids))
        bloques_or.append(f"t.empleado_id IN ({placeholders})")
        params_turnos.extend(empleados_base_ids)

    filtro_operativo = []

    if centro_id:
        # Si el turno trae centro, debe coincidir; si no lo trae, no lo descartamos para evitar impresos vacíos antiguos.
        filtro_operativo.append("(t.centro_id IS NULL OR t.centro_id = ?)")
        params_turnos.append(centro_id)

    if departamento_id:
        filtro_operativo.append("COALESCE(t.departamento_id, e.departamento_id) = ?")
        params_turnos.append(departamento_id)

    if subdepartamento_id:
        filtro_operativo.append("COALESCE(t.subdepartamento_id, e.subdepartamento_id) = ?")
        params_turnos.append(subdepartamento_id)

    if puesto_id:
        filtro_operativo.append("COALESCE(t.puesto_id, 0) = ?")
        params_turnos.append(puesto_id)

    if not _usuario_es_global_rrHH() if False else False:
        pass

    if not _usuario_es_global_rrhh() and _session_departamento_id():
        filtro_operativo.append("COALESCE(t.departamento_id, e.departamento_id) = ?")
        params_turnos.append(_session_departamento_id())

    if filtro_operativo:
        bloques_or.append("(" + " AND ".join(filtro_operativo) + ")")

    if bloques_or:
        where_turnos.append("(" + " OR ".join(bloques_or) + ")")

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
        WHERE {' AND '.join(where_turnos)}
        ORDER BY date(t.fecha), e.nombre, t.hora_inicio
        """,
        params_turnos,
    ).fetchall()

    # ------------------------------------------------------------
    # 3) Empleados finales = empleados base + empleados con turno filtrado
    # ------------------------------------------------------------
    ids_turnos = [t["empleado_id"] for t in turnos]
    ids_finales = sorted(set(empleados_base_ids) | set(ids_turnos))

    empleados = []
    if ids_finales:
        placeholders = ",".join(["?"] * len(ids_finales))
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
            [empresa_id] + ids_finales,
        ).fetchall()

    # ------------------------------------------------------------
    # 4) Vacaciones/ausencias de los empleados visibles
    # ------------------------------------------------------------
    vacaciones = []
    if ids_finales:
        placeholders = ",".join(["?"] * len(ids_finales))
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
            [empresa_id] + ids_finales + [fin.isoformat(), inicio.isoformat()],
        ).fetchall()

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

    turnos_por_empleado_dia = {}
    for t in turnos:
        turno = dict(t)
        turnos_por_empleado_dia.setdefault(
            (turno["empleado_id"], turno["fecha"]),
            [],
        ).append(turno)

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

    # ------------------------------------------------------------
    # 5) Resumen operativo por categoría/puesto y día
    # ------------------------------------------------------------
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

        categoria = (
            turno.get("puesto_nombre")
            or turno.get("categoria_nombre")
            or "Sin categoría"
        ).strip()
        empleado_id = turno.get("empleado_id")

        resumen_sets.setdefault(categoria, {}).setdefault(fecha, set()).add(empleado_id)
        total_dia_sets[fecha].add(empleado_id)

    resumen_categorias = []
    for categoria in sorted(resumen_sets.keys(), key=lambda x: x.lower()):
        fila = {"categoria": categoria, "dias": {}, "total_semana": 0}
        semana_empleados = set()
        for dia in dias:
            fecha = dia.isoformat()
            empleados_dia = resumen_sets.get(categoria, {}).get(fecha, set())
            fila["dias"][fecha] = len(empleados_dia)
            semana_empleados.update(empleados_dia)
        fila["total_semana"] = len(semana_empleados)
        resumen_categorias.append(fila)

    total_por_dia = {}
    for dia in dias:
        fecha = dia.isoformat()
        total_por_dia[fecha] = len(total_dia_sets.get(fecha, set()))

    todos_operativos_semana = set()
    for ids in total_dia_sets.values():
        todos_operativos_semana.update(ids)
    total_semana_operativos = len(todos_operativos_semana)

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

text = routes_path.read_text(encoding="utf-8")
pattern = re.compile(
    r"\n# ============================================================\n# IMPRESIÓN PROFESIONAL CUADRANTE REAL\n# ============================================================\n@rrhh_bp\.route\(\"/cuadrantes/real/imprimir\"\)\s*@login_required\s*def imprimir_cuadrante_real\(\):.*?(?=\n# ============================================================|\Z)",
    re.S,
)

if pattern.search(text):
    text = pattern.sub("\n" + funcion.strip() + "\n", text)
else:
    text = text.rstrip() + "\n\n" + funcion.strip() + "\n"

# Limpieza de un typo defensivo que nunca debe quedar en el código.
text = text.replace("\n    if not _usuario_es_global_rrHH() if False else False:\n        pass\n", "\n")

routes_path.write_text(text, encoding="utf-8")

template_dst.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(template_src, template_dst)

print("OK: impresión de cuadrante parcheada.")
print(f"Backup creado en: {backup}")
print(f"Template copiado a: {template_dst}")

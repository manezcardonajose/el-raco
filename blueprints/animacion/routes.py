from datetime import date, datetime, timedelta

from flask import flash, redirect, render_template, request, session, url_for

from database import get_db
from blueprints.auth.routes import login_required, usuario_es_global_operacion, usuario_tiene_permiso
from . import animacion_bp


TIPOS_ACTIVIDAD = [
    ("infantil", "Infantil"),
    ("familiar", "Familiar"),
    ("deportiva", "Deportiva"),
    ("show", "Show"),
    ("musica", "Musica"),
    ("wellness", "Wellness"),
    ("excursion", "Excursion"),
    ("otro", "Otro"),
]

PUBLICOS = [
    ("todos", "Todos"),
    ("ninos", "Ninos"),
    ("adolescentes", "Adolescentes"),
    ("adultos", "Adultos"),
    ("familias", "Familias"),
    ("senior", "Senior"),
]

MODELOS_PRESTACION = [
    ("propia", "Propia"),
    ("subcontratada", "Subcontratada"),
    ("mixta", "Mixta"),
]


def _empresa_id():
    try:
        return int(session.get("user_empresa_id") or session.get("empresa_id") or 1)
    except (TypeError, ValueError):
        return 1


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn, table_name, column_name):
    if not _table_exists(conn, table_name):
        return False
    try:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return False
    for col in cols:
        try:
            name = col["name"]
        except Exception:
            name = col[1]
        if name == column_name:
            return True
    return False


def _add_column_if_missing(conn, table_name, column_sql):
    column_name = column_sql.strip().split()[0]
    if _table_exists(conn, table_name) and not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def _to_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _row_value(row, key, default=None):
    try:
        return row[key]
    except Exception:
        return default


def _parse_date(value, default=None):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    texto = str(value or "").strip()
    if not texto:
        return default
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(texto[:19] if "%H" in fmt else texto[:10], fmt).date()
        except ValueError:
            continue
    return default


def _date_range(desde, hasta):
    actual = desde
    while actual <= hasta:
        yield actual
        actual += timedelta(days=1)


def _puede_ver_animacion():
    return (
        usuario_es_global_operacion()
        or usuario_tiene_permiso("animacion_ver")
        or usuario_tiene_permiso("animacion_gestionar")
        or usuario_tiene_permiso("recepcion_ver")
        or usuario_tiene_permiso("informes_ver")
    )


def _puede_gestionar_animacion():
    return usuario_es_global_operacion() or usuario_tiene_permiso("animacion_gestionar")


def _coste_total_actividad(actividad):
    coste_real = _to_float(_row_value(actividad, "coste_real"), 0.0)
    if coste_real:
        return coste_real
    coste_desglosado = (
        _to_float(_row_value(actividad, "coste_personal_interno"), 0.0)
        + _to_float(_row_value(actividad, "coste_proveedor"), 0.0)
        + _to_float(_row_value(actividad, "coste_materiales"), 0.0)
    )
    if coste_desglosado:
        return coste_desglosado
    return _to_float(_row_value(actividad, "coste_estimado"), 0.0)


def _proveedores(conn, empresa_id):
    if not _table_exists(conn, "proveedores"):
        return []
    return conn.execute(
        """
        SELECT id, nombre
        FROM proveedores
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY nombre
        """,
        (empresa_id,),
    ).fetchall()


def _ensure_animacion_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS animacion_actividades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            hora_inicio TEXT,
            hora_fin TEXT,
            titulo TEXT NOT NULL,
            tipo TEXT DEFAULT 'otro',
            publico TEXT DEFAULT 'todos',
            idioma TEXT DEFAULT 'multi',
            ubicacion TEXT,
            capacidad INTEGER DEFAULT 0,
            responsable TEXT,
            coste_estimado REAL DEFAULT 0,
            coste_real REAL DEFAULT 0,
            modelo_prestacion TEXT DEFAULT 'propia',
            proveedor_id INTEGER,
            coste_personal_interno REAL DEFAULT 0,
            coste_proveedor REAL DEFAULT 0,
            coste_materiales REAL DEFAULT 0,
            ingreso_directo REAL DEFAULT 0,
            venta_indirecta_estimada REAL DEFAULT 0,
            contrato_referencia TEXT,
            factura_documento_id INTEGER,
            estado TEXT DEFAULT 'planificada',
            observaciones TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "fecha TEXT", "hora_inicio TEXT", "hora_fin TEXT", "titulo TEXT", "tipo TEXT DEFAULT 'otro'",
        "publico TEXT DEFAULT 'todos'", "idioma TEXT DEFAULT 'multi'", "ubicacion TEXT",
        "capacidad INTEGER DEFAULT 0", "responsable TEXT", "coste_estimado REAL DEFAULT 0",
        "coste_real REAL DEFAULT 0", "modelo_prestacion TEXT DEFAULT 'propia'", "proveedor_id INTEGER",
        "coste_personal_interno REAL DEFAULT 0", "coste_proveedor REAL DEFAULT 0",
        "coste_materiales REAL DEFAULT 0", "ingreso_directo REAL DEFAULT 0",
        "venta_indirecta_estimada REAL DEFAULT 0", "contrato_referencia TEXT",
        "factura_documento_id INTEGER", "estado TEXT DEFAULT 'planificada'", "observaciones TEXT",
        "departamento_id INTEGER", "subdepartamento_id INTEGER", "punto_venta_id INTEGER",
        "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "animacion_actividades", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS animacion_asistencias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actividad_id INTEGER NOT NULL,
            reserva_id INTEGER,
            viajero_id INTEGER,
            habitacion TEXT,
            cliente_nombre TEXT,
            adultos INTEGER DEFAULT 0,
            menores INTEGER DEFAULT 0,
            pax INTEGER DEFAULT 1,
            satisfaccion INTEGER,
            comentarios TEXT,
            empresa_id INTEGER DEFAULT 1,
            usuario_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (actividad_id) REFERENCES animacion_actividades(id) ON DELETE CASCADE
        )
        """
    )
    for col in [
        "actividad_id INTEGER", "reserva_id INTEGER", "viajero_id INTEGER", "habitacion TEXT",
        "cliente_nombre TEXT", "adultos INTEGER DEFAULT 0", "menores INTEGER DEFAULT 0",
        "pax INTEGER DEFAULT 1", "satisfaccion INTEGER", "comentarios TEXT",
        "empresa_id INTEGER DEFAULT 1", "usuario_id INTEGER", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "animacion_asistencias", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS animacion_recursos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actividad_id INTEGER NOT NULL,
            recurso TEXT NOT NULL,
            cantidad REAL DEFAULT 1,
            coste REAL DEFAULT 0,
            proveedor_id INTEGER,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (actividad_id) REFERENCES animacion_actividades(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()


def _reservas_periodo(conn, empresa_id, desde, hasta):
    if not _table_exists(conn, "reservas_recepcion"):
        return []
    if _table_exists(conn, "habitaciones_recepcion"):
        habitacion_select = "COALESCE(h.numero, h.nombre, '') AS habitacion"
        habitacion_join = "LEFT JOIN habitaciones_recepcion h ON h.id = r.habitacion_id"
    else:
        habitacion_select = "'' AS habitacion"
        habitacion_join = ""
    return conn.execute(
        f"""
        SELECT r.id, r.codigo, r.fecha_entrada, r.fecha_salida, r.titular_nombre,
               r.adultos, r.menores, r.regimen,
               {habitacion_select}
        FROM reservas_recepcion r
        {habitacion_join}
        WHERE r.empresa_id = ?
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) >= date(?)
          AND lower(COALESCE(r.estado, 'confirmada')) NOT IN ('cancelada', 'cancelado', 'no_show')
        ORDER BY date(r.fecha_entrada), r.titular_nombre
        """,
        (empresa_id, hasta.isoformat(), desde.isoformat()),
    ).fetchall()


def _prevision_animacion(conn, empresa_id, desde, hasta):
    reservas = _reservas_periodo(conn, empresa_id, desde, hasta)
    pax_noches = 0
    menores_noches = 0
    entradas = 0
    for dia in _date_range(desde, hasta):
        for reserva in reservas:
            entrada = _parse_date(reserva["fecha_entrada"])
            salida = _parse_date(reserva["fecha_salida"])
            if entrada == dia:
                entradas += 1
            if entrada and salida and entrada <= dia < salida:
                pax_noches += _to_int(reserva["adultos"]) + _to_int(reserva["menores"])
                menores_noches += _to_int(reserva["menores"])
    return {
        "reservas": len(reservas),
        "pax_noches": pax_noches,
        "menores_noches": menores_noches,
        "entradas": entradas,
        "reservas_lista": reservas,
    }


def _actividades_periodo(conn, empresa_id, desde, hasta):
    proveedor_select = "pr.nombre AS proveedor_nombre" if _table_exists(conn, "proveedores") else "NULL AS proveedor_nombre"
    proveedor_join = "LEFT JOIN proveedores pr ON pr.id = a.proveedor_id" if _table_exists(conn, "proveedores") else ""
    return conn.execute(
        f"""
        SELECT a.*,
               {proveedor_select},
               COALESCE(SUM(aa.pax), 0) AS asistentes,
               COALESCE(SUM(aa.menores), 0) AS menores_asistentes,
               AVG(aa.satisfaccion) AS satisfaccion_media
        FROM animacion_actividades a
        LEFT JOIN animacion_asistencias aa ON aa.actividad_id = a.id
        {proveedor_join}
        WHERE a.empresa_id = ?
          AND date(a.fecha) BETWEEN date(?) AND date(?)
        GROUP BY a.id
        ORDER BY date(a.fecha), a.hora_inicio, a.titulo
        """,
        (empresa_id, desde.isoformat(), hasta.isoformat()),
    ).fetchall()


def _resumen_animacion(actividades, prevision):
    coste_estimado = sum(_to_float(a["coste_estimado"], 0.0) for a in actividades)
    coste_real = sum(_coste_total_actividad(a) for a in actividades)
    coste_personal_interno = sum(_to_float(_row_value(a, "coste_personal_interno"), 0.0) for a in actividades)
    coste_proveedor = sum(_to_float(_row_value(a, "coste_proveedor"), 0.0) for a in actividades)
    coste_materiales = sum(_to_float(_row_value(a, "coste_materiales"), 0.0) for a in actividades)
    ingreso_directo = sum(_to_float(_row_value(a, "ingreso_directo"), 0.0) for a in actividades)
    venta_indirecta_estimada = sum(_to_float(_row_value(a, "venta_indirecta_estimada"), 0.0) for a in actividades)
    asistentes = sum(_to_int(a["asistentes"], 0) for a in actividades)
    infantiles = sum(1 for a in actividades if (a["publico"] or "") in {"ninos", "familias"} or (a["tipo"] or "") == "infantil")
    satisfacciones = [_to_float(a["satisfaccion_media"], 0.0) for a in actividades if a["satisfaccion_media"] is not None]
    por_modelo = {"propia": 0.0, "subcontratada": 0.0, "mixta": 0.0}
    conteo_modelo = {"propia": 0, "subcontratada": 0, "mixta": 0}
    for actividad in actividades:
        modelo = (_row_value(actividad, "modelo_prestacion") or "propia").strip().lower()
        if modelo not in por_modelo:
            modelo = "propia"
        por_modelo[modelo] += _coste_total_actividad(actividad)
        conteo_modelo[modelo] += 1
    return {
        "actividades": len(actividades),
        "infantiles": infantiles,
        "asistentes": asistentes,
        "coste_estimado": coste_estimado,
        "coste_real": coste_real,
        "coste_personal_interno": coste_personal_interno,
        "coste_proveedor": coste_proveedor,
        "coste_materiales": coste_materiales,
        "coste_propia": por_modelo["propia"],
        "coste_subcontratada": por_modelo["subcontratada"],
        "coste_mixta": por_modelo["mixta"],
        "propias": conteo_modelo["propia"],
        "subcontratadas": conteo_modelo["subcontratada"],
        "mixtas": conteo_modelo["mixta"],
        "ingreso_directo": ingreso_directo,
        "venta_indirecta_estimada": venta_indirecta_estimada,
        "resultado_directo": ingreso_directo - coste_real,
        "impacto_estimado": ingreso_directo + venta_indirecta_estimada - coste_real,
        "coste_por_asistente": (coste_real / asistentes) if asistentes else 0,
        "satisfaccion_media": (sum(satisfacciones) / len(satisfacciones)) if satisfacciones else 0,
        "pax_noches": prevision["pax_noches"],
        "menores_noches": prevision["menores_noches"],
        "reservas": prevision["reservas"],
        "entradas": prevision["entradas"],
    }


@animacion_bp.route("/")
@login_required
def dashboard():
    if not _puede_ver_animacion():
        flash("No tienes permisos para ver Animacion.", "warning")
        return redirect(url_for("home"))
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_animacion_schema(conn)
    hoy = date.today()
    desde = _parse_date(request.args.get("desde"), hoy) or hoy
    hasta = _parse_date(request.args.get("hasta"), desde + timedelta(days=7)) or (desde + timedelta(days=7))
    if hasta < desde:
        hasta = desde
    prevision = _prevision_animacion(conn, empresa_id, desde, hasta)
    actividades = _actividades_periodo(conn, empresa_id, desde, hasta)
    resumen = _resumen_animacion(actividades, prevision)
    return render_template(
        "animacion/dashboard.html",
        actividades=actividades,
        resumen=resumen,
        prevision=prevision,
        desde=desde.isoformat(),
        hasta=hasta.isoformat(),
        tipos=TIPOS_ACTIVIDAD,
        publicos=PUBLICOS,
        modelos_prestacion=MODELOS_PRESTACION,
        puede_gestionar=_puede_gestionar_animacion(),
    )


@animacion_bp.route("/actividades/nueva", methods=["GET", "POST"])
@login_required
def nueva_actividad():
    if not _puede_gestionar_animacion():
        flash("No tienes permisos para crear actividades.", "warning")
        return redirect(url_for("animacion.dashboard"))
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_animacion_schema(conn)
    if request.method == "POST":
        titulo = (request.form.get("titulo") or "").strip()
        fecha = request.form.get("fecha") or date.today().isoformat()
        if not titulo:
            flash("El titulo de la actividad es obligatorio.", "warning")
            return redirect(url_for("animacion.nueva_actividad"))
        conn.execute(
            """
            INSERT INTO animacion_actividades (
                fecha, hora_inicio, hora_fin, titulo, tipo, publico, idioma,
                ubicacion, capacidad, responsable, coste_estimado, coste_real,
                modelo_prestacion, proveedor_id, coste_personal_interno,
                coste_proveedor, coste_materiales, ingreso_directo,
                venta_indirecta_estimada, contrato_referencia, estado,
                observaciones, empresa_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fecha,
                request.form.get("hora_inicio") or "",
                request.form.get("hora_fin") or "",
                titulo,
                request.form.get("tipo") or "otro",
                request.form.get("publico") or "todos",
                request.form.get("idioma") or "multi",
                request.form.get("ubicacion") or "",
                _to_int(request.form.get("capacidad"), 0),
                request.form.get("responsable") or "",
                _to_float(request.form.get("coste_estimado"), 0.0),
                _to_float(request.form.get("coste_real"), 0.0),
                request.form.get("modelo_prestacion") or "propia",
                request.form.get("proveedor_id") or None,
                _to_float(request.form.get("coste_personal_interno"), 0.0),
                _to_float(request.form.get("coste_proveedor"), 0.0),
                _to_float(request.form.get("coste_materiales"), 0.0),
                _to_float(request.form.get("ingreso_directo"), 0.0),
                _to_float(request.form.get("venta_indirecta_estimada"), 0.0),
                request.form.get("contrato_referencia") or "",
                request.form.get("estado") or "planificada",
                request.form.get("observaciones") or "",
                empresa_id,
            ),
        )
        conn.commit()
        flash("Actividad de animacion creada.", "success")
        return redirect(url_for("animacion.dashboard", desde=fecha, hasta=fecha))
    return render_template(
        "animacion/form_actividad.html",
        actividad=None,
        tipos=TIPOS_ACTIVIDAD,
        publicos=PUBLICOS,
        modelos_prestacion=MODELOS_PRESTACION,
        proveedores=_proveedores(conn, empresa_id),
    )


@animacion_bp.route("/actividades/<int:actividad_id>", methods=["GET", "POST"])
@login_required
def detalle_actividad(actividad_id):
    if not _puede_ver_animacion():
        flash("No tienes permisos para ver Animacion.", "warning")
        return redirect(url_for("home"))
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_animacion_schema(conn)
    actividad = conn.execute(
        "SELECT * FROM animacion_actividades WHERE id = ? AND empresa_id = ?",
        (actividad_id, empresa_id),
    ).fetchone()
    if not actividad:
        flash("Actividad no encontrada.", "warning")
        return redirect(url_for("animacion.dashboard"))

    if request.method == "POST":
        if not _puede_gestionar_animacion():
            flash("No tienes permisos para editar actividades.", "warning")
            return redirect(url_for("animacion.detalle_actividad", actividad_id=actividad_id))
        conn.execute(
            """
            UPDATE animacion_actividades
            SET fecha = ?, hora_inicio = ?, hora_fin = ?, titulo = ?, tipo = ?,
                publico = ?, idioma = ?, ubicacion = ?, capacidad = ?, responsable = ?,
                coste_estimado = ?, coste_real = ?, modelo_prestacion = ?, proveedor_id = ?,
                coste_personal_interno = ?, coste_proveedor = ?, coste_materiales = ?,
                ingreso_directo = ?, venta_indirecta_estimada = ?, contrato_referencia = ?,
                estado = ?, observaciones = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (
                request.form.get("fecha") or actividad["fecha"],
                request.form.get("hora_inicio") or "",
                request.form.get("hora_fin") or "",
                (request.form.get("titulo") or actividad["titulo"]).strip(),
                request.form.get("tipo") or "otro",
                request.form.get("publico") or "todos",
                request.form.get("idioma") or "multi",
                request.form.get("ubicacion") or "",
                _to_int(request.form.get("capacidad"), 0),
                request.form.get("responsable") or "",
                _to_float(request.form.get("coste_estimado"), 0.0),
                _to_float(request.form.get("coste_real"), 0.0),
                request.form.get("modelo_prestacion") or "propia",
                request.form.get("proveedor_id") or None,
                _to_float(request.form.get("coste_personal_interno"), 0.0),
                _to_float(request.form.get("coste_proveedor"), 0.0),
                _to_float(request.form.get("coste_materiales"), 0.0),
                _to_float(request.form.get("ingreso_directo"), 0.0),
                _to_float(request.form.get("venta_indirecta_estimada"), 0.0),
                request.form.get("contrato_referencia") or "",
                request.form.get("estado") or "planificada",
                request.form.get("observaciones") or "",
                actividad_id,
                empresa_id,
            ),
        )
        conn.commit()
        flash("Actividad actualizada.", "success")
        return redirect(url_for("animacion.detalle_actividad", actividad_id=actividad_id))

    asistencias = conn.execute(
        """
        SELECT *
        FROM animacion_asistencias
        WHERE actividad_id = ? AND empresa_id = ?
        ORDER BY fecha_creacion DESC
        """,
        (actividad_id, empresa_id),
    ).fetchall()
    reservas = _reservas_periodo(conn, empresa_id, _parse_date(actividad["fecha"], date.today()), _parse_date(actividad["fecha"], date.today()))
    return render_template(
        "animacion/detalle_actividad.html",
        actividad=actividad,
        asistencias=asistencias,
        reservas=reservas,
        tipos=TIPOS_ACTIVIDAD,
        publicos=PUBLICOS,
        modelos_prestacion=MODELOS_PRESTACION,
        proveedores=_proveedores(conn, empresa_id),
        puede_gestionar=_puede_gestionar_animacion(),
    )


@animacion_bp.route("/actividades/<int:actividad_id>/asistencia", methods=["POST"])
@login_required
def registrar_asistencia(actividad_id):
    if not _puede_ver_animacion():
        flash("No tienes permisos para registrar asistencia.", "warning")
        return redirect(url_for("home"))
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_animacion_schema(conn)
    actividad = conn.execute(
        "SELECT id FROM animacion_actividades WHERE id = ? AND empresa_id = ?",
        (actividad_id, empresa_id),
    ).fetchone()
    if not actividad:
        flash("Actividad no encontrada.", "warning")
        return redirect(url_for("animacion.dashboard"))
    conn.execute(
        """
        INSERT INTO animacion_asistencias (
            actividad_id, reserva_id, habitacion, cliente_nombre, adultos, menores,
            pax, satisfaccion, comentarios, empresa_id, usuario_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actividad_id,
            request.form.get("reserva_id") or None,
            request.form.get("habitacion") or "",
            request.form.get("cliente_nombre") or "",
            _to_int(request.form.get("adultos"), 0),
            _to_int(request.form.get("menores"), 0),
            _to_int(request.form.get("pax"), 1),
            _to_int(request.form.get("satisfaccion"), None),
            request.form.get("comentarios") or "",
            empresa_id,
            session.get("user_id"),
        ),
    )
    conn.commit()
    flash("Asistencia registrada.", "success")
    return redirect(url_for("animacion.detalle_actividad", actividad_id=actividad_id))

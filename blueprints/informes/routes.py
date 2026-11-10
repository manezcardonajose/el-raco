from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
from flask import flash, redirect, render_template, request, send_file, session, url_for

from blueprints.auth.routes import login_required
from database import get_db
from helpers.centros_helper import centro_activo_id, ids_departamentos_centro, ids_puntos_venta_centro
from . import informes_bp


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _columns(conn, table_name):
    try:
        return [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
    except Exception:
        return []


def _one(conn, sql, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return default
    if not row:
        return default
    try:
        value = row[0]
    except Exception:
        value = next(iter(dict(row).values()), default)
    return default if value is None else value


def _rows(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def _eur(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " EUR"


def _scope_productos(alias, conn, empresa_id):
    centro_id = centro_activo_id()
    if not centro_id:
        return f"{alias}.empresa_id = ?", [empresa_id]
    condiciones = []
    params = []
    if "centro_id" in _columns(conn, "productos"):
        condiciones.append(f"{alias}.centro_id = ?")
        params.append(centro_id)
    dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
    if dep_ids and "departamento_responsable_id" in _columns(conn, "productos"):
        condiciones.append(f"{alias}.departamento_responsable_id IN ({','.join('?' for _ in dep_ids)})")
        params.extend(dep_ids)
    if not condiciones:
        return f"{alias}.empresa_id = ? AND 1 = 0", [empresa_id]
    return f"{alias}.empresa_id = ? AND ({' OR '.join(condiciones)})", [empresa_id] + params


def _scope_almacenes(alias, conn, empresa_id):
    centro_id = centro_activo_id()
    if not centro_id:
        return f"{alias}.empresa_id = ?", [empresa_id]
    condiciones = []
    params = []
    if "centro_id" in _columns(conn, "almacenes"):
        condiciones.append(f"{alias}.centro_id = ?")
        params.append(centro_id)
    dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
    if dep_ids and "departamento_id" in _columns(conn, "almacenes"):
        condiciones.append(f"{alias}.departamento_id IN ({','.join('?' for _ in dep_ids)})")
        params.extend(dep_ids)
    if not condiciones:
        return f"{alias}.empresa_id = ? AND 1 = 0", [empresa_id]
    return f"{alias}.empresa_id = ? AND ({' OR '.join(condiciones)})", [empresa_id] + params


def _scope_pedidos(alias, conn, empresa_id):
    centro_id = centro_activo_id()
    if not centro_id:
        return f"{alias}.empresa_id = ?", [empresa_id]
    cols = _columns(conn, "pedidos")
    dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
    pv_ids = ids_puntos_venta_centro(conn, empresa_id, centro_id)
    condiciones = []
    params = []
    for col in ("departamento_solicitante_id", "departamento_id"):
        if dep_ids and col in cols:
            condiciones.append(f"{alias}.{col} IN ({','.join('?' for _ in dep_ids)})")
            params.extend(dep_ids)
    if pv_ids and "punto_venta_id" in cols:
        condiciones.append(f"{alias}.punto_venta_id IN ({','.join('?' for _ in pv_ids)})")
        params.extend(pv_ids)
    if not condiciones:
        return f"{alias}.empresa_id = ? AND 1 = 0", [empresa_id]
    return f"{alias}.empresa_id = ? AND ({' OR '.join(condiciones)})", [empresa_id] + params


def _scope_partes(alias, conn, empresa_id):
    centro_id = centro_activo_id()
    if not centro_id:
        return f"{alias}.empresa_id = ?", [empresa_id]
    cols = _columns(conn, "partes_produccion")
    condiciones = []
    params = []
    if "centro_id" in cols:
        condiciones.append(f"{alias}.centro_id = ?")
        params.append(centro_id)
    sub_ids = []
    if _table_exists(conn, "subdepartamentos"):
        sub_ids = [
            row["id"]
            for row in conn.execute(
                """
                SELECT id
                FROM subdepartamentos
                WHERE empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1
                """,
                (empresa_id, centro_id),
            ).fetchall()
        ]
    if sub_ids and "subdepartamento_id" in cols:
        condiciones.append(f"{alias}.subdepartamento_id IN ({','.join('?' for _ in sub_ids)})")
        params.extend(sub_ids)
    pv_ids = ids_puntos_venta_centro(conn, empresa_id, centro_id)
    if pv_ids and "punto_venta_id" in cols:
        condiciones.append(f"{alias}.punto_venta_id IN ({','.join('?' for _ in pv_ids)})")
        params.extend(pv_ids)
    if not condiciones:
        return f"{alias}.empresa_id = ? AND 1 = 0", [empresa_id]
    return f"{alias}.empresa_id = ? AND ({' OR '.join(condiciones)})", [empresa_id] + params


def exportar_informe_compras(datos, nombre_hoja, nombre_archivo):
    df = pd.DataFrame([dict(row) for row in datos]) if datos else pd.DataFrame()
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=nombre_hoja, index=False)
    output.seek(0)
    return send_file(
        output,
        download_name=nombre_archivo,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@informes_bp.route("/")
@login_required
def dashboard():
    return redirect(url_for("informes.ejecutivo"))


@informes_bp.route("/ejecutivo")
@login_required
def ejecutivo():
    conn = get_db()
    empresa_id = session.get("user_empresa_id", 1)
    centro_id = centro_activo_id()
    fecha_desde = request.args.get("desde", (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))
    fecha_hasta = request.args.get("hasta", datetime.now().strftime("%Y-%m-%d"))
    params_periodo = [fecha_desde, fecha_hasta, empresa_id]
    centro_sql = " AND centro_id = ?" if centro_id else ""
    centro_params = [centro_id] if centro_id else []

    kpis = []
    recomendaciones = []

    if _table_exists(conn, "simulador_vida_kpis_diarios"):
        row = conn.execute(
            f"""
            SELECT COUNT(*) dias,
                   COALESCE(SUM(ingresos_habitaciones),0) ingresos_habitaciones,
                   COALESCE(SUM(ingresos_tpv),0) ingresos_tpv,
                   COALESCE(SUM(coste_laboral_estimado),0) coste_laboral,
                   COALESCE(SUM(coste_compras_estimado),0) coste_compras,
                   COALESCE(SUM(coste_fijo_estimado),0) coste_fijo,
                   COALESCE(SUM(coste_total_estimado),0) coste_total,
                   COALESCE(SUM(margen_estimado),0) margen,
                   COALESCE(AVG(habitaciones_ocupadas),0) habitaciones_media,
                   COALESCE(SUM(quejas),0) quejas,
                   COALESCE(SUM(ordenes_mantenimiento),0) mantenimiento,
                   COALESCE(SUM(no_conformidades_appcc),0) appcc
            FROM simulador_vida_kpis_diarios
            WHERE fecha BETWEEN ? AND ? AND empresa_id=?
              {centro_sql}
            """,
            params_periodo + centro_params,
        ).fetchone()
        ingresos = float((row["ingresos_habitaciones"] or 0) + (row["ingresos_tpv"] or 0))
        coste_total = float(row["coste_total"] or 0)
        margen = float(row["margen"] or ingresos - coste_total)
        rentabilidad = (margen / ingresos * 100) if ingresos else 0
        kpis.extend([
            {"titulo": "Ingresos", "valor": _eur(ingresos), "detalle": "Habitaciones + TPV del periodo."},
            {"titulo": "Coste total", "valor": _eur(coste_total), "detalle": "Laboral, compras y coste fijo estimado."},
            {"titulo": "Margen", "valor": _eur(margen), "detalle": f"Rentabilidad aproximada {rentabilidad:.1f}%."},
            {"titulo": "Habitaciones media", "valor": f"{float(row['habitaciones_media'] or 0):.1f}", "detalle": "Media diaria del periodo."},
        ])
        if margen < 0:
            recomendaciones.append({
                "prioridad": "critica",
                "area": "Rentabilidad",
                "titulo": "El periodo pierde dinero",
                "detalle": "Cruzar ocupacion, ADR, coste laboral y compras antes de lanzar ofertas o reforzar plantilla.",
            })
        if float(row["coste_laboral"] or 0) > ingresos * 0.38 and ingresos:
            recomendaciones.append({
                "prioridad": "alta",
                "area": "RRHH",
                "titulo": "Coste laboral alto frente a ingresos",
                "detalle": "Revisar cuadrantes, rotativos, horas punta y puestos cubiertos sin demanda real.",
            })
        if int(row["mantenimiento"] or 0) > 0:
            recomendaciones.append({
                "prioridad": "media",
                "area": "Mantenimiento",
                "titulo": "Mantenimiento afecta a capacidad vendible",
                "detalle": "Cruzar ordenes abiertas con habitaciones bloqueadas, quejas y reputacion.",
            })
        resumen_operativo = dict(row)
    else:
        resumen_operativo = {}

    compras = {}
    if _table_exists(conn, "contabilidad_documentos"):
        compras = dict(conn.execute(
            f"""
            SELECT COUNT(*) documentos,
                   COALESCE(SUM(total),0) total,
                   COALESCE(SUM(pagado),0) pagado,
                   COALESCE(SUM(pendiente),0) pendiente,
                   SUM(CASE WHEN estado='vencido' THEN 1 ELSE 0 END) vencidos,
                   SUM(CASE WHEN estado='pagado_parcial' THEN 1 ELSE 0 END) parciales
            FROM contabilidad_documentos
            WHERE empresa_id=?
              AND fecha_documento BETWEEN ? AND ?
              {('AND centro_id = ?' if centro_id else '')}
            """,
            [empresa_id, fecha_desde, fecha_hasta] + centro_params,
        ).fetchone())
        if float(compras.get("pendiente") or 0) > 0:
            recomendaciones.append({
                "prioridad": "alta" if int(compras.get("vencidos") or 0) else "media",
                "area": "Tesoreria",
                "titulo": "Hay pagos pendientes",
                "detalle": f"Pendiente actual en documentos del periodo: {_eur(compras.get('pendiente'))}.",
            })

    modulos = [
        ("Reservas", "reservas_recepcion", "fecha_entrada"),
        ("TPV", "tpv_cuentas", "fecha_apertura"),
        ("Compras", "pedidos", "fecha_pedido"),
        ("Recepciones", "recepciones", "fecha_recepcion"),
        ("Produccion", "partes_produccion", "fecha"),
        ("Incidencias", "incidencias", "fecha"),
        ("Mantenimiento", "ordenes_trabajo", "fecha_apertura"),
        ("Fichajes", "fichajes", "fecha_hora"),
    ]
    cobertura = []
    for nombre, tabla, fecha_col in modulos:
        if not _table_exists(conn, tabla):
            cobertura.append({"modulo": nombre, "tabla": tabla, "registros": 0, "estado": "sin_tabla", "lectura": "No hay tabla."})
            continue
        cols = _columns(conn, tabla)
        where = ["1=1"]
        params = []
        if "empresa_id" in cols:
            where.append("empresa_id=?")
            params.append(empresa_id)
        if fecha_col in cols:
            where.append(f"date({fecha_col}) BETWEEN date(?) AND date(?)")
            params.extend([fecha_desde, fecha_hasta])
        if centro_id and "centro_id" in cols:
            where.append("centro_id=?")
            params.append(centro_id)
        total = _one(conn, f"SELECT COUNT(*) FROM {tabla} WHERE {' AND '.join(where)}", params)
        estado = "ok" if int(total or 0) else "vacio"
        cobertura.append({
            "modulo": nombre,
            "tabla": tabla,
            "registros": int(total or 0),
            "estado": estado,
            "lectura": "Aporta datos al informe." if estado == "ok" else "Sin datos en el periodo.",
        })

    top_alertas = []
    if _table_exists(conn, "simulador_vida_eventos"):
        top_alertas = _rows(
            conn,
            f"""
            SELECT fecha_simulada, modulo, tipo, severidad, descripcion
            FROM simulador_vida_eventos
            WHERE empresa_id=? AND date(fecha_simulada) BETWEEN date(?) AND date(?)
              {('AND centro_id = ?' if centro_id else '')}
            ORDER BY CASE severidad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END,
                     fecha_simulada DESC, id DESC
            LIMIT 12
            """,
            [empresa_id, fecha_desde, fecha_hasta] + centro_params,
        )

    diagnostico_ia = [
        {"bloque": "Chat estrategico", "estado": "mantener", "lectura": "Debe ser la puerta de direccion, pero con respuestas basadas en datos e indicando huecos."},
        {"bloque": "Radar estrategico", "estado": "mantener", "lectura": "Sirve para oportunidades, siempre que cada idea tenga evidencia interna/externa y plan piloto."},
        {"bloque": "Simulador decision", "estado": "simplificar", "lectura": "Demasiado formulario confunde. Debe empezar por modelo de negocio y objetivo, y pedir solo lo que falte."},
        {"bloque": "Tendencias/aprendizaje", "estado": "ordenar", "lectura": "No debe mezclarse opinion con dato. Todo enlace debe quedar como fuente pendiente de contraste."},
        {"bloque": "IA cocina/buffet", "estado": "separar", "lectura": "Es operativa, no estrategica. Conviene dejarla en cocina/buffet con reglas y APPCC."},
    ]

    if not recomendaciones:
        recomendaciones.append({
            "prioridad": "info",
            "area": "Direccion",
            "titulo": "No hay alertas fuertes",
            "detalle": "El informe no detecta roturas graves en el periodo, pero aun falta contraste externo y objetivos reales.",
        })

    conn.close()
    return render_template(
        "informes/ejecutivo.html",
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        kpis=kpis,
        compras=compras,
        resumen_operativo=resumen_operativo,
        cobertura=cobertura,
        recomendaciones=recomendaciones,
        top_alertas=top_alertas,
        diagnostico_ia=diagnostico_ia,
    )


@informes_bp.route("/compras")
@login_required
def compras():
    return render_template("informes/compras/compras.html")


@informes_bp.route("/compras/por-producto")
@login_required
def compras_por_producto():
    conn = get_db()
    empresa_id = session.get("user_empresa_id", 1)
    fecha_desde = request.args.get("desde", (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))
    fecha_hasta = request.args.get("hasta", datetime.now().strftime("%Y-%m-%d"))
    pedidos_scope, pedidos_params = _scope_pedidos("pe", conn, empresa_id)
    productos_scope, productos_params = _scope_productos("p", conn, empresa_id)
    datos = conn.execute(
        f"""
        SELECT p.nombre AS producto, p.codigo, p.unidad,
               COUNT(DISTINCT pe.id) AS num_pedidos,
               COALESCE(SUM(lp.cantidad_solicitada), 0) AS total_cantidad,
               COALESCE(SUM(lp.cantidad_solicitada * lp.precio_unitario), 0) AS total_gasto,
               COALESCE(AVG(lp.precio_unitario), 0) AS precio_medio
        FROM lineas_pedido lp
        JOIN productos p ON lp.producto_id = p.id
        JOIN pedidos pe ON lp.pedido_id = pe.id
        WHERE date(pe.fecha_pedido) BETWEEN ? AND ?
          AND {pedidos_scope}
          AND {productos_scope}
        GROUP BY p.id
        ORDER BY total_gasto DESC
        """,
        [fecha_desde, fecha_hasta] + pedidos_params + productos_params,
    ).fetchall()
    conn.close()
    if request.args.get("exportar") == "excel":
        return exportar_informe_compras(datos, "compras_por_producto", f"compras_producto_{fecha_desde}_a_{fecha_hasta}.xlsx")
    return render_template("informes/compras/compras_por_producto.html", datos=datos, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta)


@informes_bp.route("/compras/historico")
@login_required
def compras_historico():
    conn = get_db()
    empresa_id = session.get("user_empresa_id", 1)
    year = request.args.get("año") or request.args.get("anio") or datetime.now().strftime("%Y")
    pedidos_scope, pedidos_params = _scope_pedidos("pe", conn, empresa_id)
    datos = conn.execute(
        f"""
        SELECT strftime('%m', pe.fecha_pedido) AS mes,
               COUNT(DISTINCT pe.id) AS num_pedidos,
               COUNT(lp.id) AS num_lineas,
               COALESCE(SUM(lp.cantidad_solicitada * lp.precio_unitario), 0) AS total_mes
        FROM pedidos pe
        LEFT JOIN lineas_pedido lp ON pe.id = lp.pedido_id
        WHERE strftime('%Y', pe.fecha_pedido) = ?
          AND {pedidos_scope}
        GROUP BY strftime('%m', pe.fecha_pedido)
        ORDER BY mes
        """,
        [year] + pedidos_params,
    ).fetchall()
    conn.close()
    return render_template("informes/compras/historico_compras.html", datos=datos, año=year, años=[year, str(int(year) - 1), str(int(year) - 2)])


@informes_bp.route("/compras/comparativa-precios")
@login_required
def compras_comparativa():
    conn = get_db()
    empresa_id = session.get("user_empresa_id", 1)
    producto_id = request.args.get("producto_id", "")
    productos_scope, productos_params = _scope_productos("p", conn, empresa_id)
    query = f"""
        SELECT p.nombre AS producto, pr.nombre AS proveedor, COALESCE(pp.precio, pp.precio_compra, pp.precio_vigente, pp.precio_ultimo, 0) AS precio,
               pp.es_principal, pr.telefono, pr.email
        FROM productos_proveedores pp
        JOIN productos p ON pp.producto_id = p.id
        JOIN proveedores pr ON pp.proveedor_id = pr.id
        WHERE {productos_scope}
    """
    params = list(productos_params)
    if producto_id:
        query += " AND p.id = ?"
        params.append(producto_id)
    query += " ORDER BY p.nombre, pr.nombre"
    datos = conn.execute(query, params).fetchall()
    productos = conn.execute(f"SELECT id, nombre FROM productos p WHERE {productos_scope} ORDER BY nombre", productos_params).fetchall()
    conn.close()
    return render_template("informes/compras/comparativa_precios.html", datos=datos, productos=productos, producto_seleccionado=producto_id)


@informes_bp.route("/compras/evolucion-precios")
@login_required
def compras_evolucion():
    conn = get_db()
    empresa_id = session.get("user_empresa_id", 1)
    producto_id = request.args.get("producto_id")
    if not producto_id:
        flash("Debes seleccionar un producto.", "danger")
        conn.close()
        return redirect(url_for("informes.compras"))
    productos_scope, productos_params = _scope_productos("p", conn, empresa_id)
    producto = conn.execute(f"SELECT p.nombre FROM productos p WHERE p.id = ? AND {productos_scope}", [producto_id] + productos_params).fetchone()
    if not producto:
        flash("Producto no encontrado en el centro activo.", "warning")
        conn.close()
        return redirect(url_for("informes.compras"))
    pedidos_scope, pedidos_params = _scope_pedidos("pe", conn, empresa_id)
    datos = conn.execute(
        f"""
        SELECT pe.fecha_pedido, pr.nombre AS proveedor,
               lp.precio_unitario AS precio, lp.cantidad_solicitada AS cantidad
        FROM lineas_pedido lp
        JOIN pedidos pe ON lp.pedido_id = pe.id
        JOIN proveedores pr ON pe.proveedor_id = pr.id
        WHERE lp.producto_id = ?
          AND {pedidos_scope}
        ORDER BY pe.fecha_pedido DESC
        LIMIT 50
        """,
        [producto_id] + pedidos_params,
    ).fetchall()
    conn.close()
    return render_template("informes/compras/evolucion_precios.html", datos=datos, producto=producto)


@informes_bp.route("/stock")
@login_required
def stock():
    conn = get_db()
    empresa_id = session.get("user_empresa_id", 1)
    productos_scope, productos_params = _scope_productos("p", conn, empresa_id)
    almacenes_scope, almacenes_params = _scope_almacenes("a", conn, empresa_id)
    total_productos = conn.execute(f"SELECT COUNT(*) AS total FROM productos p WHERE {productos_scope} AND p.activo = 1", productos_params).fetchone()["total"]
    valor_total = conn.execute(
        f"""
        SELECT COALESCE(SUM(s.cantidad * p.precio), 0) AS total
        FROM productos p
        LEFT JOIN stock_almacen s ON s.producto_id = p.id
        LEFT JOIN almacenes a ON a.id = s.almacen_id
        WHERE {productos_scope}
          AND p.activo = 1
          AND ({almacenes_scope} OR s.almacen_id IS NULL)
        """,
        productos_params + almacenes_params,
    ).fetchone()["total"] or 0
    stock_bajo = conn.execute(
        f"""
        SELECT p.*, COALESCE(SUM(s.cantidad), 0) AS stock_actual,
               COUNT(DISTINCT s.almacen_id) AS num_almacenes
        FROM productos p
        LEFT JOIN stock_almacen s ON p.id = s.producto_id
        LEFT JOIN almacenes a ON a.id = s.almacen_id
        WHERE {productos_scope}
          AND p.activo = 1
          AND ({almacenes_scope} OR s.almacen_id IS NULL)
        GROUP BY p.id
        HAVING stock_actual <= p.stock_minimo AND stock_actual > 0
        ORDER BY (stock_actual * 1.0 / NULLIF(p.stock_minimo, 0)) ASC
        LIMIT 20
        """,
        productos_params + almacenes_params,
    ).fetchall()
    agotados = conn.execute(
        f"""
        SELECT p.*, COALESCE(SUM(s.cantidad), 0) AS stock_actual
        FROM productos p
        LEFT JOIN stock_almacen s ON p.id = s.producto_id
        LEFT JOIN almacenes a ON a.id = s.almacen_id
        WHERE {productos_scope}
          AND p.activo = 1
          AND ({almacenes_scope} OR s.almacen_id IS NULL)
        GROUP BY p.id
        HAVING stock_actual <= 0
        ORDER BY p.nombre
        LIMIT 20
        """,
        productos_params + almacenes_params,
    ).fetchall()
    stock_almacenes = conn.execute(
        f"""
        SELECT a.nombre AS almacen,
               COUNT(DISTINCT s.producto_id) AS num_productos,
               COALESCE(SUM(s.cantidad), 0) AS total_items,
               COALESCE(SUM(s.cantidad * p.precio), 0) AS valor_total
        FROM almacenes a
        LEFT JOIN stock_almacen s ON a.id = s.almacen_id
        LEFT JOIN productos p ON s.producto_id = p.id
        WHERE a.activo = 1 AND {almacenes_scope}
        GROUP BY a.id
        ORDER BY a.nombre
        """,
        almacenes_params,
    ).fetchall()
    conn.close()
    return render_template(
        "informes/stock/stock.html",
        total_productos=total_productos,
        valor_total=valor_total,
        stock_bajo=stock_bajo,
        agotados=agotados,
        stock_almacenes=stock_almacenes,
    )


@informes_bp.route("/stock/exportar")
@login_required
def stock_exportar():
    conn = get_db()
    empresa_id = session.get("user_empresa_id", 1)
    productos_scope, productos_params = _scope_productos("p", conn, empresa_id)
    almacenes_scope, almacenes_params = _scope_almacenes("a", conn, empresa_id)
    datos = conn.execute(
        f"""
        SELECT p.codigo, p.nombre AS producto, p.familia, p.tipo_conservacion,
               COALESCE(SUM(s.cantidad), 0) AS stock_actual,
               p.stock_minimo, p.unidad, p.precio,
               COALESCE(SUM(s.cantidad), 0) * p.precio AS valor_total,
               COUNT(DISTINCT s.almacen_id) AS num_almacenes
        FROM productos p
        LEFT JOIN stock_almacen s ON p.id = s.producto_id
        LEFT JOIN almacenes a ON a.id = s.almacen_id
        WHERE {productos_scope}
          AND p.activo = 1
          AND ({almacenes_scope} OR s.almacen_id IS NULL)
        GROUP BY p.id
        ORDER BY p.nombre
        """,
        productos_params + almacenes_params,
    ).fetchall()
    conn.close()
    df = pd.DataFrame([dict(row) for row in datos])
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Stock", index=False)
    output.seek(0)
    return send_file(output, download_name=f"stock_{datetime.now().strftime('%Y%m%d')}.xlsx", as_attachment=True, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@informes_bp.route("/produccion")
@login_required
def produccion():
    conn = get_db()
    empresa_id = session.get("user_empresa_id", 1)
    fecha_desde = request.args.get("desde", (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))
    fecha_hasta = request.args.get("hasta", datetime.now().strftime("%Y-%m-%d"))
    partes_scope, partes_params = _scope_partes("pp", conn, empresa_id)
    periodo_params = [fecha_desde, fecha_hasta] + partes_params
    total_partes = conn.execute(f"SELECT COUNT(*) AS total FROM partes_produccion pp WHERE pp.fecha BETWEEN ? AND ? AND {partes_scope}", periodo_params).fetchone()["total"]
    partes_completados = conn.execute(f"SELECT COUNT(*) AS total FROM partes_produccion pp WHERE pp.fecha BETWEEN ? AND ? AND pp.estado = 'completado' AND {partes_scope}", periodo_params).fetchone()["total"]
    total_lineas = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM lineas_produccion lp
        JOIN partes_produccion pp ON lp.parte_id = pp.id
        WHERE pp.fecha BETWEEN ? AND ? AND {partes_scope}
        """,
        periodo_params,
    ).fetchone()["total"]
    partes_estado = conn.execute(
        f"""
        SELECT pp.estado AS estado, COUNT(*) AS total
        FROM partes_produccion pp
        WHERE pp.fecha BETWEEN ? AND ? AND {partes_scope}
        GROUP BY pp.estado
        """,
        periodo_params,
    ).fetchall()
    produccion_subdeptos = conn.execute(
        f"""
        SELECT d.nombre AS departamento, s.nombre AS subdepartamento,
               COUNT(DISTINCT pp.id) AS num_partes,
               COUNT(lp.id) AS num_lineas,
               SUM(CASE WHEN lp.estado = 'completado' THEN 1 ELSE 0 END) AS lineas_completadas
        FROM partes_produccion pp
        LEFT JOIN subdepartamentos s ON pp.subdepartamento_id = s.id
        LEFT JOIN departamentos d ON s.departamento_id = d.id
        LEFT JOIN lineas_produccion lp ON pp.id = lp.parte_id
        WHERE pp.fecha BETWEEN ? AND ? AND {partes_scope}
        GROUP BY s.id
        ORDER BY d.nombre, s.nombre
        """,
        periodo_params,
    ).fetchall()
    top_recetas = conn.execute(
        f"""
        SELECT r.nombre AS receta, COUNT(lp.id) AS veces_producida,
               SUM(lp.cantidad_real) AS cantidad_total,
               AVG(lp.cantidad_real) AS media_por_vez
        FROM lineas_produccion lp
        JOIN recetas r ON lp.receta_id = r.id
        JOIN partes_produccion pp ON lp.parte_id = pp.id
        WHERE pp.fecha BETWEEN ? AND ? AND {partes_scope}
        GROUP BY lp.receta_id
        ORDER BY veces_producida DESC
        LIMIT 10
        """,
        periodo_params,
    ).fetchall()
    conn.close()
    return render_template(
        "informes/produccion/informes_produccion.html",
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        total_partes=total_partes,
        partes_completados=partes_completados,
        total_lineas=total_lineas,
        partes_estado=partes_estado,
        produccion_subdeptos=produccion_subdeptos,
        top_recetas=top_recetas,
    )


@informes_bp.route("/produccion/exportar")
@login_required
def produccion_exportar():
    conn = get_db()
    empresa_id = session.get("user_empresa_id", 1)
    fecha_desde = request.args.get("desde", (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))
    fecha_hasta = request.args.get("hasta", datetime.now().strftime("%Y-%m-%d"))
    partes_scope, partes_params = _scope_partes("pp", conn, empresa_id)
    datos = conn.execute(
        f"""
        SELECT pp.fecha, pp.turno, d.nombre AS departamento, s.nombre AS subdepartamento,
               r.nombre AS receta, lp.cantidad_planificada, lp.cantidad_real,
               lp.estado AS estado_linea, pp.estado AS estado_parte, pp.responsable
        FROM partes_produccion pp
        LEFT JOIN lineas_produccion lp ON pp.id = lp.parte_id
        LEFT JOIN recetas r ON lp.receta_id = r.id
        LEFT JOIN subdepartamentos s ON pp.subdepartamento_id = s.id
        LEFT JOIN departamentos d ON s.departamento_id = d.id
        WHERE pp.fecha BETWEEN ? AND ? AND {partes_scope}
        ORDER BY pp.fecha DESC, pp.turno
        """,
        [fecha_desde, fecha_hasta] + partes_params,
    ).fetchall()
    conn.close()
    df = pd.DataFrame([dict(row) for row in datos])
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Produccion", index=False)
    output.seek(0)
    return send_file(output, download_name=f"produccion_{fecha_desde}_a_{fecha_hasta}.xlsx", as_attachment=True, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

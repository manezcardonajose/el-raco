from datetime import datetime

import csv
from io import StringIO

from flask import Response, flash, redirect, render_template, request, session, url_for

from blueprints.auth.routes import (
    departamento_scope_sql,
    login_required,
    usuario_departamento_id,
    usuario_es_jefatura_departamento,
    usuario_es_global_operacion,
)
from database import get_db
from helpers.centros_helper import add_columna_si_falta, centro_activo_id, filtro_centro_almacen
from . import inventario_bp


ROLES_INVENTARIO_GLOBAL = {"almacen"}

FAMILIAS_BASE = [
    ("menaje", "Menaje"),
    ("utillaje", "Utillaje"),
    ("vajilla", "Vajilla"),
    ("cristaleria", "Cristaleria"),
    ("cuberteria", "Cuberteria"),
    ("cocina", "Cocina"),
    ("buffet", "Buffet"),
    ("textil", "Textil"),
    ("limpieza", "Limpieza"),
    ("otros", "Otros"),
]

ESTADOS_BASE = [
    ("operativo", "Operativo"),
    ("reserva", "Reserva"),
    ("reparacion", "En reparacion"),
    ("baja", "Baja"),
    ("perdido", "Perdido"),
]

USOS_OPERATIVOS = [
    ("cliente_buffet", "Cliente buffet"),
    ("servicio_buffet", "Servicio buffet"),
    ("presentacion_buffet", "Presentacion buffet"),
    ("cocina_utillaje", "Utillaje cocina"),
    ("cocina_produccion", "Produccion cocina"),
    ("reposicion", "Reposicion"),
    ("limpieza", "Limpieza"),
    ("otros", "Otros"),
]


def _empresa_id():
    return session.get("user_empresa_id", 1) or 1


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _ensure_schema(conn):
    for tabla in ('departamentos', 'almacenes', 'puntos_venta'):
        add_columna_si_falta(conn, tabla, 'centro_id INTEGER')
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventario_articulos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT,
            nombre TEXT NOT NULL,
            familia TEXT DEFAULT 'otros',
            categoria TEXT,
            unidad TEXT DEFAULT 'ud',
            descripcion TEXT,
            stock_minimo REAL DEFAULT 0,
            valor_unitario REAL DEFAULT 0,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventario_existencias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            articulo_id INTEGER NOT NULL,
            cantidad REAL DEFAULT 0,
            estado TEXT DEFAULT 'operativo',
            ubicacion TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            almacen_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventario_movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            articulo_id INTEGER NOT NULL,
            existencia_id INTEGER,
            tipo TEXT NOT NULL,
            cantidad REAL DEFAULT 0,
            cantidad_anterior REAL DEFAULT 0,
            cantidad_nueva REAL DEFAULT 0,
            motivo TEXT,
            usuario TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for table in ("inventario_articulos", "inventario_existencias", "inventario_movimientos"):
        add_columna_si_falta(conn, table, "centro_id INTEGER")
    for ddl in [
        "uso_operativo TEXT DEFAULT 'otros'",
        "material TEXT",
        "formato TEXT",
        "capacidad TEXT",
        "compatible_appcc INTEGER DEFAULT 1",
        "apto_caliente INTEGER DEFAULT 0",
        "apto_frio INTEGER DEFAULT 0",
        "apto_horno INTEGER DEFAULT 0",
        "apto_lavavajillas INTEGER DEFAULT 1",
        "rompible INTEGER DEFAULT 0",
        "requiere_reposicion_buffet INTEGER DEFAULT 0",
    ]:
        add_columna_si_falta(conn, "inventario_articulos", ddl)
    conn.execute(
        """
        UPDATE inventario_existencias
        SET centro_id = COALESCE(
            centro_id,
            (SELECT centro_id FROM almacenes a WHERE a.id = inventario_existencias.almacen_id),
            (SELECT centro_id FROM departamentos d WHERE d.id = inventario_existencias.departamento_id),
            (SELECT centro_id FROM puntos_venta pv WHERE pv.id = inventario_existencias.punto_venta_id)
        )
        WHERE centro_id IS NULL
        """
    )


def _puede_gestionar():
    return usuario_es_global_operacion(ROLES_INVENTARIO_GLOBAL) or usuario_es_jefatura_departamento()


def _float(value, default=0.0):
    try:
        return float(str(value or "").replace(",", "."))
    except Exception:
        return default


def _int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except Exception:
        return None


def _bool_arg(name):
    return request.args.get(name) in {"1", "true", "on", "si", "yes"}


def _centro_para_ubicacion(conn, empresa_id, departamento_id=None, punto_venta_id=None, almacen_id=None):
    if almacen_id and _table_exists(conn, "almacenes"):
        row = conn.execute(
            "SELECT centro_id FROM almacenes WHERE id = ? AND empresa_id = ?",
            (almacen_id, empresa_id),
        ).fetchone()
        if row and row["centro_id"]:
            return row["centro_id"]
    if departamento_id and _table_exists(conn, "departamentos"):
        row = conn.execute(
            "SELECT centro_id FROM departamentos WHERE id = ? AND empresa_id = ?",
            (departamento_id, empresa_id),
        ).fetchone()
        if row and row["centro_id"]:
            return row["centro_id"]
    if punto_venta_id and _table_exists(conn, "puntos_venta"):
        row = conn.execute(
            "SELECT centro_id FROM puntos_venta WHERE id = ? AND empresa_id = ?",
            (punto_venta_id, empresa_id),
        ).fetchone()
        if row and row["centro_id"]:
            return row["centro_id"]
    return centro_activo_id()


def _opciones(conn, empresa_id):
    centro_id = centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro_id else ""
    centro_params = [centro_id] if centro_id else []
    dep_where = ["COALESCE(activo, 1) = 1", "empresa_id = ?"]
    dep_params = [empresa_id]
    if centro_id:
        dep_where.append("centro_id = ?")
        dep_params.append(centro_id)
    if not usuario_es_global_operacion(ROLES_INVENTARIO_GLOBAL):
        departamento_id = usuario_departamento_id()
        if departamento_id:
            dep_where.append("id = ?")
            dep_params.append(departamento_id)
        else:
            dep_where.append("1 = 0")
    almacenes_sql, almacenes_params = filtro_centro_almacen(conn, "a", empresa_id)
    return {
        "departamentos": conn.execute(
            f"""
            SELECT id, nombre
            FROM departamentos
            WHERE {" AND ".join(dep_where)}
            ORDER BY nombre
            """,
            dep_params,
        ).fetchall() if _table_exists(conn, "departamentos") else [],
        "subdepartamentos": conn.execute(
            f"""
            SELECT id, nombre, departamento_id
            FROM subdepartamentos
            WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
              {centro_sql}
            ORDER BY nombre
            """,
            [empresa_id] + centro_params,
        ).fetchall() if _table_exists(conn, "subdepartamentos") else [],
        "puntos_venta": conn.execute(
            f"""
            SELECT id, nombre
            FROM puntos_venta
            WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
              {centro_sql}
            ORDER BY nombre
            """,
            [empresa_id] + centro_params,
        ).fetchall() if _table_exists(conn, "puntos_venta") else [],
        "almacenes": conn.execute(
            f"""
            SELECT a.id, a.nombre
            FROM almacenes a
            WHERE a.empresa_id = ? AND COALESCE(a.activo, 1) = 1
              {almacenes_sql}
            ORDER BY a.nombre
            """,
            [empresa_id] + almacenes_params,
        ).fetchall() if _table_exists(conn, "almacenes") else [],
    }


def _inventario_query(conn, empresa_id, filtros):
    busqueda = filtros.get("buscar") or ""
    familia = filtros.get("familia") or ""
    estado = filtros.get("estado") or ""
    uso = filtros.get("uso_operativo") or ""
    bajo_minimo = filtros.get("bajo_minimo")
    centro_id = centro_activo_id()
    where = ["ia.empresa_id = ?", "COALESCE(ia.activo, 1) = 1"]
    params = [empresa_id]
    if centro_id:
        where.append("ia.centro_id = ?")
        params.append(centro_id)
    if busqueda:
        where.append(
            "(ia.nombre LIKE ? OR COALESCE(ia.codigo, '') LIKE ? OR COALESCE(ia.categoria, '') LIKE ? "
            "OR COALESCE(ia.material, '') LIKE ? OR COALESCE(ia.formato, '') LIKE ?)"
        )
        like = f"%{busqueda}%"
        params.extend([like, like, like, like, like])
    if familia:
        where.append("ia.familia = ?")
        params.append(familia)
    if uso:
        where.append("COALESCE(ia.uso_operativo, 'otros') = ?")
        params.append(uso)
    if estado:
        where.append("COALESCE(ie.estado, 'operativo') = ?")
        params.append(estado)
    having = "HAVING cantidad_total < COALESCE(ia.stock_minimo, 0)" if bajo_minimo else ""
    return conn.execute(
        f"""
        SELECT ia.*,
               COALESCE(SUM(ie.cantidad), 0) AS cantidad_total,
               COUNT(DISTINCT ie.id) AS ubicaciones,
               GROUP_CONCAT(DISTINCT COALESCE(a.nombre, pv.nombre, sd.nombre, d.nombre, ie.ubicacion)) AS ubicaciones_texto
        FROM inventario_articulos ia
        LEFT JOIN inventario_existencias ie ON ie.articulo_id = ia.id
        LEFT JOIN almacenes a ON a.id = ie.almacen_id
        LEFT JOIN puntos_venta pv ON pv.id = ie.punto_venta_id
        LEFT JOIN subdepartamentos sd ON sd.id = ie.subdepartamento_id
        LEFT JOIN departamentos d ON d.id = ie.departamento_id
        WHERE {" AND ".join(where)}
        GROUP BY ia.id
        {having}
        ORDER BY
          CASE WHEN COALESCE(SUM(ie.cantidad), 0) < COALESCE(ia.stock_minimo, 0) THEN 0 ELSE 1 END,
          ia.familia, ia.uso_operativo, ia.nombre
        """,
        params,
    ).fetchall()


@inventario_bp.route("/", methods=["GET", "POST"])
@login_required
def inventario():
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_schema(conn)
    conn.commit()

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El articulo necesita nombre.", "warning")
            conn.close()
            return redirect(url_for("inventario.inventario"))
        centro_id = centro_activo_id()
        conn.execute(
            """
            INSERT INTO inventario_articulos
            (codigo, nombre, familia, categoria, unidad, descripcion, stock_minimo,
             valor_unitario, uso_operativo, material, formato, capacidad,
             compatible_appcc, apto_caliente, apto_frio, apto_horno, apto_lavavajillas,
             rompible, requiere_reposicion_buffet, activo, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                (request.form.get("codigo") or "").strip() or None,
                nombre,
                request.form.get("familia") or "otros",
                (request.form.get("categoria") or "").strip() or None,
                (request.form.get("unidad") or "ud").strip() or "ud",
                (request.form.get("descripcion") or "").strip() or None,
                _float(request.form.get("stock_minimo")),
                _float(request.form.get("valor_unitario")),
                request.form.get("uso_operativo") or "otros",
                (request.form.get("material") or "").strip() or None,
                (request.form.get("formato") or "").strip() or None,
                (request.form.get("capacidad") or "").strip() or None,
                1 if request.form.get("compatible_appcc") else 0,
                1 if request.form.get("apto_caliente") else 0,
                1 if request.form.get("apto_frio") else 0,
                1 if request.form.get("apto_horno") else 0,
                1 if request.form.get("apto_lavavajillas") else 0,
                1 if request.form.get("rompible") else 0,
                1 if request.form.get("requiere_reposicion_buffet") else 0,
                empresa_id,
                centro_id,
            ),
        )
        conn.commit()
        conn.close()
        flash("Articulo de inventario creado.", "success")
        return redirect(url_for("inventario.inventario"))

    busqueda = (request.args.get("buscar") or "").strip()
    familia = (request.args.get("familia") or "").strip()
    estado = (request.args.get("estado") or "").strip()
    uso_operativo = (request.args.get("uso_operativo") or "").strip()
    bajo_minimo = _bool_arg("bajo_minimo")
    centro_id = centro_activo_id()
    filtros = {
        "buscar": busqueda,
        "familia": familia,
        "estado": estado,
        "uso_operativo": uso_operativo,
        "bajo_minimo": bajo_minimo,
    }
    rows = _inventario_query(conn, empresa_id, filtros)
    valor_total = sum((_float(row["cantidad_total"]) * _float(row["valor_unitario"])) for row in rows)
    bajo_minimo_total = sum(1 for row in rows if _float(row["cantidad_total"]) < _float(row["stock_minimo"]))
    articulos = conn.execute(
        """
        SELECT id, nombre, codigo
        FROM inventario_articulos
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
          AND (? IS NULL OR centro_id = ?)
        ORDER BY nombre
        """,
        (empresa_id, centro_id, centro_id),
    ).fetchall()
    opciones = _opciones(conn, empresa_id)
    conn.close()
    return render_template(
        "inventario/inventario.html",
        rows=rows,
        articulos=articulos,
        opciones=opciones,
        familias=FAMILIAS_BASE,
        usos_operativos=USOS_OPERATIVOS,
        estados=ESTADOS_BASE,
        filtros=filtros,
        valor_total=valor_total,
        bajo_minimo_total=bajo_minimo_total,
        puede_gestionar=_puede_gestionar(),
    )


@inventario_bp.route("/revista.csv")
@login_required
def revista_csv():
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_schema(conn)
    filtros = {
        "buscar": (request.args.get("buscar") or "").strip(),
        "familia": (request.args.get("familia") or "").strip(),
        "estado": (request.args.get("estado") or "").strip(),
        "uso_operativo": (request.args.get("uso_operativo") or "").strip(),
        "bajo_minimo": _bool_arg("bajo_minimo"),
    }
    rows = _inventario_query(conn, empresa_id, filtros)
    conn.close()
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "codigo", "articulo", "familia", "uso_operativo", "categoria", "material",
        "formato", "capacidad", "cantidad_sistema", "conteo_real", "diferencia",
        "unidad", "stock_minimo", "ubicaciones", "valor_unitario", "observaciones",
    ])
    for row in rows:
        writer.writerow([
            row["codigo"] or "",
            row["nombre"] or "",
            row["familia"] or "",
            row["uso_operativo"] or "",
            row["categoria"] or "",
            row["material"] or "",
            row["formato"] or "",
            row["capacidad"] or "",
            f"{_float(row['cantidad_total']):.2f}",
            "",
            "",
            row["unidad"] or "ud",
            f"{_float(row['stock_minimo']):.2f}",
            row["ubicaciones_texto"] or "",
            f"{_float(row['valor_unitario']):.2f}",
            "",
        ])
    filename = f"revista_inventario_centro_{centro_activo_id() or 'general'}.csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@inventario_bp.route("/revista")
@login_required
def revista():
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_schema(conn)
    filtros = {
        "buscar": (request.args.get("buscar") or "").strip(),
        "familia": (request.args.get("familia") or "").strip(),
        "estado": (request.args.get("estado") or "").strip(),
        "uso_operativo": (request.args.get("uso_operativo") or "").strip(),
        "bajo_minimo": _bool_arg("bajo_minimo"),
    }
    rows = _inventario_query(conn, empresa_id, filtros)
    resumen = {}
    for row in rows:
        uso = row["uso_operativo"] or "otros"
        familia = row["familia"] or "otros"
        key = (uso, familia)
        resumen.setdefault(key, {"uso": uso, "familia": familia, "articulos": 0, "unidades": 0, "valor": 0, "bajo_minimo": 0})
        resumen[key]["articulos"] += 1
        resumen[key]["unidades"] += _float(row["cantidad_total"])
        resumen[key]["valor"] += _float(row["cantidad_total"]) * _float(row["valor_unitario"])
        if _float(row["cantidad_total"]) < _float(row["stock_minimo"]):
            resumen[key]["bajo_minimo"] += 1
    conn.close()
    return render_template(
        "inventario/revista.html",
        rows=rows,
        resumen=sorted(resumen.values(), key=lambda x: (x["uso"], x["familia"])),
        familias=FAMILIAS_BASE,
        usos_operativos=USOS_OPERATIVOS,
        estados=ESTADOS_BASE,
        filtros=filtros,
        puede_gestionar=_puede_gestionar(),
    )


@inventario_bp.route("/revista/ajustar-total", methods=["POST"])
@login_required
def ajustar_total_revista():
    if not _puede_gestionar():
        flash("Solo jefatura o almacen puede ajustar inventario.", "warning")
        return redirect(url_for("inventario.revista"))
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_schema(conn)
    articulo_id = _int_or_none(request.form.get("articulo_id"))
    cantidad_real = max(0.0, _float(request.form.get("cantidad_real")))
    motivo = (request.form.get("motivo") or "").strip() or "Revista fisica de inventario"
    centro_id = centro_activo_id()
    articulo = conn.execute(
        """
        SELECT *
        FROM inventario_articulos
        WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1
          AND (? IS NULL OR centro_id = ?)
        """,
        (articulo_id, empresa_id, centro_id, centro_id),
    ).fetchone()
    if not articulo:
        conn.close()
        flash("Articulo no encontrado para la revista.", "warning")
        return redirect(request.referrer or url_for("inventario.revista"))

    existencias = conn.execute(
        """
        SELECT *
        FROM inventario_existencias
        WHERE articulo_id = ? AND empresa_id = ?
          AND (? IS NULL OR centro_id = ?)
        ORDER BY cantidad DESC, id
        """,
        (articulo_id, empresa_id, centro_id, centro_id),
    ).fetchall()
    anterior_total = sum(_float(row["cantidad"]) for row in existencias)
    delta = round(cantidad_real - anterior_total, 2)
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def registrar(existencia_id, tipo, cantidad, anterior, nueva, centro_mov):
        conn.execute(
            """
            INSERT INTO inventario_movimientos
            (articulo_id, existencia_id, tipo, cantidad, cantidad_anterior, cantidad_nueva,
             motivo, usuario, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                articulo_id, existencia_id, tipo, round(abs(cantidad), 2), round(anterior, 2),
                round(nueva, 2), motivo, session.get("user_nombre") or "admin", empresa_id, centro_mov,
            ),
        )

    if not existencias:
        cur = conn.execute(
            """
            INSERT INTO inventario_existencias
            (articulo_id, cantidad, estado, ubicacion, empresa_id, centro_id, updated_at)
            VALUES (?, ?, 'operativo', 'Revista fisica', ?, ?, ?)
            """,
            (articulo_id, round(cantidad_real, 2), empresa_id, centro_id, ahora),
        )
        registrar(cur.lastrowid, "conteo", cantidad_real, 0, cantidad_real, centro_id)
    elif delta > 0:
        row = existencias[0]
        nueva = _float(row["cantidad"]) + delta
        conn.execute(
            "UPDATE inventario_existencias SET cantidad = ?, updated_at = ? WHERE id = ? AND empresa_id = ?",
            (round(nueva, 2), ahora, row["id"], empresa_id),
        )
        registrar(row["id"], "entrada", delta, _float(row["cantidad"]), nueva, row["centro_id"] or centro_id)
    elif delta < 0:
        pendiente = abs(delta)
        for row in existencias:
            if pendiente <= 0:
                break
            anterior = _float(row["cantidad"])
            descuento = min(anterior, pendiente)
            nueva = anterior - descuento
            conn.execute(
                "UPDATE inventario_existencias SET cantidad = ?, updated_at = ? WHERE id = ? AND empresa_id = ?",
                (round(nueva, 2), ahora, row["id"], empresa_id),
            )
            registrar(row["id"], "salida", descuento, anterior, nueva, row["centro_id"] or centro_id)
            pendiente = round(pendiente - descuento, 2)

    conn.commit()
    conn.close()
    if delta:
        flash(f"Revista guardada para {articulo['nombre']}: diferencia {delta:+.2f}.", "success")
    else:
        flash(f"Revista guardada para {articulo['nombre']}: sin diferencias.", "info")
    return redirect(request.referrer or url_for("inventario.revista"))


@inventario_bp.route("/ajustar", methods=["POST"])
@login_required
def ajustar():
    if not _puede_gestionar():
        flash("Solo jefatura o almacen puede ajustar inventario.", "warning")
        return redirect(url_for("inventario.inventario"))
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_schema(conn)
    articulo_id = _int_or_none(request.form.get("articulo_id"))
    articulo = conn.execute(
        "SELECT * FROM inventario_articulos WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1",
        (articulo_id, empresa_id),
    ).fetchone()
    if not articulo:
        conn.close()
        flash("Articulo no encontrado.", "danger")
        return redirect(url_for("inventario.inventario"))

    departamento_id = _int_or_none(request.form.get("departamento_id"))
    subdepartamento_id = _int_or_none(request.form.get("subdepartamento_id"))
    punto_venta_id = _int_or_none(request.form.get("punto_venta_id"))
    almacen_id = _int_or_none(request.form.get("almacen_id"))
    centro_id = _centro_para_ubicacion(conn, empresa_id, departamento_id, punto_venta_id, almacen_id)
    if centro_activo_id() and centro_id != centro_activo_id():
        conn.close()
        flash("La ubicacion no pertenece al centro activo.", "warning")
        return redirect(url_for("inventario.inventario"))

    estado = request.form.get("estado") or "operativo"
    cantidad = _float(request.form.get("cantidad"))
    tipo = request.form.get("tipo") or "conteo"
    ubicacion = (request.form.get("ubicacion") or "").strip() or None
    motivo = (request.form.get("motivo") or "").strip() or "Ajuste inventario"

    existencia = conn.execute(
        """
        SELECT *
        FROM inventario_existencias
        WHERE articulo_id = ?
          AND empresa_id = ?
          AND COALESCE(centro_id, 0) = COALESCE(?, 0)
          AND COALESCE(departamento_id, 0) = COALESCE(?, 0)
          AND COALESCE(subdepartamento_id, 0) = COALESCE(?, 0)
          AND COALESCE(punto_venta_id, 0) = COALESCE(?, 0)
          AND COALESCE(almacen_id, 0) = COALESCE(?, 0)
          AND COALESCE(estado, '') = COALESCE(?, '')
        LIMIT 1
        """,
        (articulo_id, empresa_id, centro_id, departamento_id, subdepartamento_id, punto_venta_id, almacen_id, estado),
    ).fetchone()
    anterior = _float(existencia["cantidad"]) if existencia else 0.0
    if tipo == "entrada":
        nueva = anterior + cantidad
    elif tipo == "salida":
        nueva = max(0.0, anterior - cantidad)
    else:
        nueva = max(0.0, cantidad)

    if existencia:
        existencia_id = existencia["id"]
        conn.execute(
            """
            UPDATE inventario_existencias
            SET cantidad = ?, ubicacion = COALESCE(?, ubicacion), updated_at = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (round(nueva, 2), ubicacion, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), existencia_id, empresa_id),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO inventario_existencias
            (articulo_id, cantidad, estado, ubicacion, departamento_id, subdepartamento_id,
             punto_venta_id, almacen_id, empresa_id, centro_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                articulo_id,
                round(nueva, 2),
                estado,
                ubicacion,
                departamento_id,
                subdepartamento_id,
                punto_venta_id,
                almacen_id,
                empresa_id,
                centro_id,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        existencia_id = cur.lastrowid
    conn.execute(
        """
        INSERT INTO inventario_movimientos
        (articulo_id, existencia_id, tipo, cantidad, cantidad_anterior, cantidad_nueva,
         motivo, usuario, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            articulo_id,
            existencia_id,
            tipo,
            round(cantidad, 2),
            round(anterior, 2),
            round(nueva, 2),
            motivo,
            session.get("user_nombre") or "admin",
            empresa_id,
            centro_id,
        ),
    )
    conn.commit()
    conn.close()
    flash("Inventario ajustado.", "success")
    return redirect(url_for("inventario.inventario"))

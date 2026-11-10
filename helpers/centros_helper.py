from flask import session


def centro_activo_id():
    # Un valor explicito vacio representa la vista general de empresa.
    if "centro_activo_id" in session:
        try:
            valor = session.get("centro_activo_id")
            return int(valor) if valor else None
        except (ValueError, TypeError):
            return None
    for clave in ("centro_activo_id", "user_centro_id", "centro_id"):
        valor = session.get(clave)
        try:
            if valor:
                return int(valor)
        except Exception:
            continue
    return None


def columna_existe(conn, tabla, columna):
    try:
        columnas = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
    except Exception:
        return False
    return any((c["name"] if hasattr(c, "keys") and "name" in c.keys() else c[1]) == columna for c in columnas)


def add_columna_si_falta(conn, tabla, definicion):
    nombre = definicion.strip().split()[0]
    if columna_existe(conn, tabla, nombre):
        return
    conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {definicion}")


def ids_departamentos_centro(conn, empresa_id, centro_id=None):
    centro_id = centro_id if centro_id is not None else centro_activo_id()
    if not centro_id:
        return []
    if not columna_existe(conn, "departamentos", "centro_id"):
        return []
    return [
        r["id"]
        for r in conn.execute(
            """
            SELECT id
            FROM departamentos
            WHERE empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1
            """,
            (empresa_id, centro_id),
        ).fetchall()
    ]


def ids_puntos_venta_centro(conn, empresa_id, centro_id=None):
    centro_id = centro_id if centro_id is not None else centro_activo_id()
    if not centro_id or not columna_existe(conn, "puntos_venta", "centro_id"):
        return []
    return [
        r["id"]
        for r in conn.execute(
            """
            SELECT id
            FROM puntos_venta
            WHERE empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1
            """,
            (empresa_id, centro_id),
        ).fetchall()
    ]


def filtro_centro_por_departamento(conn, alias, empresa_id, tabla, columnas_departamento=None, columnas_punto_venta=None):
    centro_id = centro_activo_id()
    if not centro_id:
        return "", []

    columnas_departamento = ["departamento_id"] if columnas_departamento is None else columnas_departamento
    columnas_punto_venta = ["punto_venta_id"] if columnas_punto_venta is None else columnas_punto_venta
    dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
    pv_ids = ids_puntos_venta_centro(conn, empresa_id, centro_id)
    condiciones = []
    params = []

    for columna in columnas_departamento:
        if dep_ids and columna_existe(conn, tabla, columna):
            condiciones.append(f"{alias}.{columna} IN ({','.join('?' for _ in dep_ids)})")
            params.extend(dep_ids)

    for columna in columnas_punto_venta:
        if pv_ids and columna_existe(conn, tabla, columna):
            condiciones.append(f"{alias}.{columna} IN ({','.join('?' for _ in pv_ids)})")
            params.extend(pv_ids)

    if columna_existe(conn, tabla, "centro_id"):
        if condiciones:
            relaciones = ' OR '.join(condiciones)
            return f" AND ({alias}.centro_id = ? OR ({alias}.centro_id IS NULL AND ({relaciones})))", [centro_id] + params
        return f" AND {alias}.centro_id = ?", [centro_id]

    if not condiciones:
        return " AND 1 = 0", []
    return f" AND ({' OR '.join(condiciones)})", params


def filtro_centro_almacen(conn, alias, empresa_id):
    return filtro_centro_por_departamento(
        conn,
        alias,
        empresa_id,
        "almacenes",
        columnas_departamento=["departamento_id"],
        columnas_punto_venta=[],
    )


def asignar_centro_desde_departamento(conn, departamento_id, centro_id=None):
    if centro_id:
        return centro_id
    if not departamento_id or not columna_existe(conn, "departamentos", "centro_id"):
        return centro_activo_id()
    row = conn.execute("SELECT centro_id FROM departamentos WHERE id = ?", (departamento_id,)).fetchone()
    return row["centro_id"] if row and row["centro_id"] else centro_activo_id()

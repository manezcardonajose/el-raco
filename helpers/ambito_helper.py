from flask import session

from helpers.centros_helper import centro_activo_id, columna_existe, ids_departamentos_centro, ids_puntos_venta_centro


ROLES_GLOBALES_OPERACION = {
    "admin",
    "administrador",
    "gerente",
    "director",
    "direccion",
    "direccion_general",
    "director_operaciones",
    "director_hotel",
    "subdirector",
    "propietario",
    "contable",
    "contabilidad",
    "administracion",
}

ROLES_JEFATURA_DEPARTAMENTO = {
    "jefe_departamento",
    "segundo_jefe",
    "jefe_cocina",
    "chef",
    "metre",
    "maitre",
    "jefe_bares",
    "gobernanta",
    "jefe_mantenimiento",
    "segundo_mantenimiento",
    "jefe_recepcion",
}


def _rol_key(valor):
    texto = str(valor or "").strip().lower()
    for origen, destino in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        texto = texto.replace(origen, destino)
    for separador in (" ", "-", "/", "."):
        texto = texto.replace(separador, "_")
    while "__" in texto:
        texto = texto.replace("__", "_")
    return texto.strip("_")


def usuario_global_ambito(extra_roles=None):
    if session.get("user_centro_bloqueado") in (1, True, "1", "true", "True"):
        return False
    roles = set(ROLES_GLOBALES_OPERACION)
    if extra_roles:
        roles.update(_rol_key(rol) for rol in extra_roles)
    rol = _rol_key(session.get("user_rol"))
    return rol in roles


def departamento_usuario_id():
    return session.get("user_departamento_id") or session.get("departamento_id")


def subdepartamento_usuario_id():
    return session.get("user_subdepartamento_id") or session.get("subdepartamento_id")


def usuario_jefatura_departamento():
    roles = {_rol_key(session.get("user_rol")), _rol_key(session.get("user_empleado_rol"))}
    return bool(roles & ROLES_JEFATURA_DEPARTAMENTO)


def ids_departamentos_ambito(conn, empresa_id, centro_id=None, extra_roles=None):
    centro_id = centro_id if centro_id is not None else centro_activo_id()
    if usuario_global_ambito(extra_roles):
        return ids_departamentos_centro(conn, empresa_id, centro_id) if centro_id else []
    departamento_id = departamento_usuario_id()
    if not departamento_id:
        return []
    if centro_id and not columna_existe(conn, "departamentos", "centro_id"):
        return []
    if centro_id and columna_existe(conn, "departamentos", "centro_id"):
        row = conn.execute(
            """
            SELECT id
            FROM departamentos
            WHERE id = ? AND empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1
            """,
            (departamento_id, empresa_id, centro_id),
        ).fetchone()
        return [departamento_id] if row else []
    row = conn.execute(
        "SELECT id FROM departamentos WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1",
        (departamento_id, empresa_id),
    ).fetchone()
    return [departamento_id] if row else []


def ids_subdepartamentos_ambito(conn, empresa_id, centro_id=None, dep_ids=None, extra_roles=None):
    if usuario_global_ambito(extra_roles):
        return []
    subdepartamento_id = subdepartamento_usuario_id()
    if not columna_existe(conn, "subdepartamentos", "departamento_id"):
        return []
    centro_id = centro_id if centro_id is not None else centro_activo_id()
    if centro_id and not columna_existe(conn, "subdepartamentos", "centro_id"):
        return []
    dep_ids = dep_ids or ids_departamentos_ambito(conn, empresa_id, centro_id, extra_roles)
    if not dep_ids:
        return []
    if usuario_jefatura_departamento():
        centro_sql = " AND centro_id = ?" if centro_id and columna_existe(conn, "subdepartamentos", "centro_id") else ""
        params = [empresa_id] + dep_ids + ([centro_id] if centro_sql else [])
        return [
            row["id"]
            for row in conn.execute(
                f"""
                SELECT id
                FROM subdepartamentos
                WHERE empresa_id = ?
                  AND departamento_id IN ({','.join('?' for _ in dep_ids)})
                  {centro_sql}
                  AND COALESCE(activo, 1) = 1
                """,
                params,
            ).fetchall()
        ]
    if not subdepartamento_id:
        return []
    centro_sql = " AND centro_id = ?" if centro_id and columna_existe(conn, "subdepartamentos", "centro_id") else ""
    params = [subdepartamento_id, empresa_id] + dep_ids + ([centro_id] if centro_sql else [])
    row = conn.execute(
        f"""
        SELECT id
        FROM subdepartamentos
        WHERE id = ?
          AND empresa_id = ?
          AND departamento_id IN ({','.join('?' for _ in dep_ids)})
          {centro_sql}
          AND COALESCE(activo, 1) = 1
        """,
        params,
    ).fetchone()
    return [subdepartamento_id] if row else []


def ids_puntos_venta_ambito(conn, empresa_id, centro_id=None, dep_ids=None, subdep_ids=None, extra_roles=None):
    centro_id = centro_id if centro_id is not None else centro_activo_id()
    if usuario_global_ambito(extra_roles):
        return ids_puntos_venta_centro(conn, empresa_id, centro_id) if centro_id else []
    if not columna_existe(conn, "puntos_venta", "departamento_id"):
        return []
    if centro_id and not columna_existe(conn, "puntos_venta", "centro_id"):
        return []
    dep_ids = dep_ids or ids_departamentos_ambito(conn, empresa_id, centro_id, extra_roles)
    subdep_ids = subdep_ids or ids_subdepartamentos_ambito(conn, empresa_id, centro_id, dep_ids, extra_roles)
    if not dep_ids:
        return []
    if not usuario_jefatura_departamento() and subdepartamento_usuario_id():
        if not subdep_ids or not columna_existe(conn, "puntos_venta", "subdepartamento_id"):
            return []
    condiciones = [f"departamento_id IN ({','.join('?' for _ in dep_ids)})"]
    params = [empresa_id] + dep_ids
    if subdep_ids and columna_existe(conn, "puntos_venta", "subdepartamento_id"):
        condiciones.append(f"subdepartamento_id IN ({','.join('?' for _ in subdep_ids)})")
        params.extend(subdep_ids)
    if centro_id and columna_existe(conn, "puntos_venta", "centro_id"):
        condiciones.append("centro_id = ?")
        params.append(centro_id)
    return [
        row["id"]
        for row in conn.execute(
            f"""
            SELECT id
            FROM puntos_venta
            WHERE empresa_id = ?
              AND {' AND '.join(condiciones)}
              AND COALESCE(activo, 1) = 1
            """,
            params,
        ).fetchall()
    ]


def filtro_ambito_operativo(
    conn,
    alias,
    empresa_id,
    tabla,
    centro_col="centro_id",
    departamento_col="departamento_id",
    subdepartamento_col="subdepartamento_id",
    punto_venta_col="punto_venta_id",
    extra_roles=None,
):
    centro_id = centro_activo_id()
    if not centro_id and usuario_global_ambito(extra_roles):
        return f"{alias}.empresa_id = ?", [empresa_id]

    condiciones = []
    params = []
    if usuario_global_ambito(extra_roles):
        if centro_id and centro_col and columna_existe(conn, tabla, centro_col):
            condiciones.append(f"{alias}.{centro_col} = ?")
            params.append(centro_id)
    else:
        dep_ids = ids_departamentos_ambito(conn, empresa_id, centro_id, extra_roles)
        subdep_ids = ids_subdepartamentos_ambito(conn, empresa_id, centro_id, dep_ids, extra_roles)
        pv_ids = ids_puntos_venta_ambito(conn, empresa_id, centro_id, dep_ids, subdep_ids, extra_roles)
        restringir_subdepartamento = bool(not usuario_jefatura_departamento() and subdepartamento_usuario_id())
        if restringir_subdepartamento and not subdep_ids:
            return f"{alias}.empresa_id = ? AND 1 = 0", [empresa_id]
        if restringir_subdepartamento and not (subdepartamento_col and columna_existe(conn, tabla, subdepartamento_col)):
            if (pv_ids and punto_venta_col and columna_existe(conn, tabla, punto_venta_col)
                    and columna_existe(conn, 'puntos_venta', 'subdepartamento_id')):
                condiciones.append(f"{alias}.{punto_venta_col} IN ({','.join('?' for _ in pv_ids)})")
                params.extend(pv_ids)
            else:
                return f"{alias}.empresa_id = ? AND 1 = 0", [empresa_id]
        if subdep_ids and subdepartamento_col and columna_existe(conn, tabla, subdepartamento_col):
            condiciones.append(f"{alias}.{subdepartamento_col} IN ({','.join('?' for _ in subdep_ids)})")
            params.extend(subdep_ids)
        if not restringir_subdepartamento and dep_ids and departamento_col and columna_existe(conn, tabla, departamento_col):
            condiciones.append(f"{alias}.{departamento_col} IN ({','.join('?' for _ in dep_ids)})")
            params.extend(dep_ids)
        if not restringir_subdepartamento and pv_ids and punto_venta_col and columna_existe(conn, tabla, punto_venta_col):
            condiciones.append(f"{alias}.{punto_venta_col} IN ({','.join('?' for _ in pv_ids)})")
            params.extend(pv_ids)
    if not condiciones:
        return f"{alias}.empresa_id = ? AND 1 = 0", [empresa_id]
    sql = f"{alias}.empresa_id = ? AND ({' OR '.join(condiciones)})"
    if centro_id and centro_col and columna_existe(conn, tabla, centro_col) and not usuario_global_ambito(extra_roles):
        sql += f" AND {alias}.{centro_col} = ?"
        params.append(centro_id)
    return sql, [empresa_id] + params

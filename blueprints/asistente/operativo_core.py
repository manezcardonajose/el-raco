"""Cerebro operativo del asistente.

Este modulo no ejecuta cambios de negocio. Interpreta una peticion,
lee un contexto ligero del centro activo y prepara una respuesta accionable
con trazabilidad. La ejecucion real debe vivir en rutas/servicios concretos
con permisos y confirmacion.
"""

import json
import re
import sqlite3
from datetime import date


INTENCIONES = {
    "nominas_borrador": {
        "modulo": "RRHH",
        "modo": "preparar_borrador",
        "riesgo": "alto",
        "palabras": ("nomina", "nominas", "prenomina", "salarios", "embargo", "pagar trabajadores"),
    },
    "crear_centro": {
        "modulo": "Estructura",
        "modo": "preparar_borrador",
        "riesgo": "medio",
        "palabras": ("crear centro", "crear hotel", "nueva empresa", "nuevo restaurante", "nuevo centro"),
    },
    "revisar_costes": {
        "modulo": "Finanzas",
        "modo": "solo_lectura",
        "riesgo": "medio",
        "palabras": ("coste", "costes", "margen", "rentabilidad", "pierde dinero", "perdemos dinero", "presupuesto"),
    },
    "pedidos_produccion": {
        "modulo": "Compras y produccion",
        "modo": "preparar_borrador",
        "riesgo": "medio",
        "palabras": ("pedido", "pedidos", "produccion", "menus", "menu semanal", "sacar pescado", "sacar carne"),
    },
    "factura_documento": {
        "modulo": "Administracion",
        "modo": "preparar_borrador",
        "riesgo": "alto",
        "palabras": ("factura", "albaran", "ticket", "documento", "adjunto", "foto", "pdf"),
    },
    "receta_coste": {
        "modulo": "Cocina",
        "modo": "preparar_borrador",
        "riesgo": "medio",
        "palabras": ("receta", "escandallo", "alergeno", "alergenos", "plato", "coctel", "cocktail"),
    },
    "prl_appcc": {
        "modulo": "PRL y APPCC",
        "modo": "solo_lectura",
        "riesgo": "alto",
        "palabras": ("prl", "riesgo", "riesgos", "appcc", "manipulador", "seguridad alimentaria", "formacion"),
    },
    "pms_overbooking": {
        "modulo": "Recepcion",
        "modo": "preparar_borrador",
        "riesgo": "alto",
        "palabras": ("overbooking", "reserva", "reservas", "habitacion", "habitaciones", "checkin", "checkout"),
    },
    "mantenimiento": {
        "modulo": "Mantenimiento",
        "modo": "preparar_borrador",
        "riesgo": "medio",
        "palabras": ("mantenimiento", "averia", "goteras", "rota", "roto", "parte", "orden de trabajo"),
    },
    "guest_service": {
        "modulo": "Experiencia cliente",
        "modo": "solo_lectura",
        "riesgo": "medio",
        "palabras": ("guest", "queja", "reclamacion", "satisfaccion", "cliente vip", "repetidor", "opinion"),
    },
    "sala_preferencias": {
        "modulo": "Sala",
        "modo": "solo_lectura",
        "riesgo": "bajo",
        "palabras": ("sala", "restaurante", "mesa", "gusto", "preferencia", "vino", "cafeteria"),
    },
    "inversion": {
        "modulo": "Direccion",
        "modo": "solo_lectura",
        "riesgo": "alto",
        "palabras": ("inversion", "capex", "comprar", "reformar", "tour operador", "turoperador", "mundiplan"),
    },
}

HERRAMIENTAS_OPERATIVAS = [
    {
        "clave": "preparar_nominas_borrador",
        "intencion": "nominas_borrador",
        "modulo": "RRHH",
        "estado": "borrador",
        "riesgo": "alto",
        "descripcion": "Cruzar empleados, contratos, fichajes, variables, embargos y nominas ya creadas.",
        "requiere": ("empleados", "contratos", "fichajes", "nominas"),
    },
    {
        "clave": "preparar_necesidades_compra",
        "intencion": "pedidos_produccion",
        "modulo": "Compras y produccion",
        "estado": "borrador",
        "riesgo": "medio",
        "descripcion": "Cruzar menus, partes, recetas, productos, stock y proveedores para proponer pedidos.",
        "requiere": ("menus", "partes_produccion", "recetas_ingredientes", "productos", "pedidos"),
    },
    {
        "clave": "revisar_ocupacion_y_bloqueos",
        "intencion": "pms_overbooking",
        "modulo": "Recepcion",
        "estado": "borrador",
        "riesgo": "alto",
        "descripcion": "Revisar reservas, habitaciones, precios, bloqueos y riesgo de overbooking.",
        "requiere": ("reservas_recepcion", "habitaciones_recepcion"),
    },
    {
        "clave": "leer_documento_y_preparar_asiento",
        "intencion": "factura_documento",
        "modulo": "Administracion",
        "estado": "borrador",
        "riesgo": "alto",
        "descripcion": "Leer factura, albaran, reserva o ticket y dejar propuesta pendiente de validacion.",
        "requiere": ("asistente_documentos_lectura", "contabilidad_documentos", "pedidos"),
    },
    {
        "clave": "revisar_coste_real_produccion",
        "intencion": "receta_coste",
        "modulo": "Cocina",
        "estado": "lectura",
        "riesgo": "medio",
        "descripcion": "Detectar recetas sin ingredientes, productos sin precio y coste real por servicio/plato.",
        "requiere": ("recetas", "recetas_ingredientes", "productos", "partes_produccion"),
    },
    {
        "clave": "preparar_orden_mantenimiento",
        "intencion": "mantenimiento",
        "modulo": "Mantenimiento",
        "estado": "borrador",
        "riesgo": "medio",
        "descripcion": "Preparar orden de trabajo con prioridad, ubicacion, responsable y seguimiento.",
        "requiere": ("ordenes_trabajo",),
    },
    {
        "clave": "revisar_formacion_prl_appcc",
        "intencion": "prl_appcc",
        "modulo": "PRL y APPCC",
        "estado": "lectura",
        "riesgo": "alto",
        "descripcion": "Comprobar formacion, manipulador, PRL, APPCC y evidencias pendientes.",
        "requiere": ("prl_formacion_empleados", "empleados"),
    },
    {
        "clave": "analizar_experiencia_cliente",
        "intencion": "guest_service",
        "modulo": "Experiencia cliente",
        "estado": "lectura",
        "riesgo": "medio",
        "descripcion": "Cruzar quejas, repetidores, preferencias, reservas y satisfaccion para proponer atenciones.",
        "requiere": ("reservas_recepcion", "asistente_recomendaciones"),
    },
]

CONTRATOS_OPERATIVOS = {
    "nominas_borrador": {
        "respuesta_tipo": "borrador_seguro",
        "salida": "prenomina y diferencias pendientes de validacion",
        "puede": (
            "cruzar empleados, contratos, fichajes, ausencias, embargos y variables",
            "preparar incidencias y prenominas en borrador",
            "explicar diferencias contra el mes anterior",
        ),
        "no_debe": (
            "cerrar nominas",
            "generar pagos definitivos",
            "enviar ficheros reales sin confirmacion humana",
        ),
        "datos": ("empleados", "contratos", "fichajes", "nominas", "embargos"),
    },
    "crear_centro": {
        "respuesta_tipo": "borrador_estructura",
        "salida": "alta propuesta de empresa, centro, departamentos y permisos",
        "puede": (
            "preguntar lo minimo necesario para crear la estructura",
            "proponer departamentos, puntos de venta, almacenes y roles",
            "dejar tareas de configuracion por modulo",
        ),
        "no_debe": (
            "mezclar centros de otra empresa",
            "activar datos reales sin revisar permisos",
            "copiar historicos sin trazabilidad",
        ),
        "datos": ("empresas", "centros", "departamentos", "puntos_venta", "usuarios"),
    },
    "revisar_costes": {
        "respuesta_tipo": "diagnostico",
        "salida": "lectura de margen con causas y palancas",
        "puede": (
            "separar ingresos, coste laboral, compras, fijos y desviaciones",
            "comparar real contra presupuesto",
            "proponer acciones medibles para recuperar margen",
        ),
        "no_debe": (
            "inventar costes recurrentes",
            "dar rentabilidad final si faltan datos base",
            "mezclar simulacion con contabilidad real",
        ),
        "datos": ("presupuestos", "gastos_recurrentes", "pedidos", "nominas", "produccion"),
    },
    "pedidos_produccion": {
        "respuesta_tipo": "borrador_compras",
        "salida": "necesidades y pedidos en borrador por proveedor",
        "puede": (
            "leer menus, recetas, stock, consumos y proveedores",
            "calcular necesidades de compra y prevision de sacar de camara",
            "avisar de recetas sin ingredientes, alergenos o precio",
        ),
        "no_debe": (
            "confirmar pedidos sin responsable",
            "dar por buena una receta vacia",
            "descontar stock definitivo sin parte validado",
        ),
        "datos": ("menus", "recetas", "productos", "stock", "proveedores", "partes_produccion"),
    },
    "factura_documento": {
        "respuesta_tipo": "lectura_documento",
        "salida": "extraccion y propuesta pendiente",
        "puede": (
            "leer documentos, fotos o adjuntos",
            "extraer proveedor, fecha, base, impuestos, centro y posible pedido",
            "preparar asiento, compra o incidencia en borrador",
        ),
        "no_debe": (
            "contabilizar sin validacion",
            "modificar stock real sin enlace a pedido o albaran",
            "enviar facturacion oficial sin control fiscal",
        ),
        "datos": ("documentos", "proveedores", "pedidos", "contabilidad", "verifactu"),
    },
    "receta_coste": {
        "respuesta_tipo": "revision_tecnica",
        "salida": "escandallo, alergenos y coste por racion revisables",
        "puede": (
            "detectar recetas vacias o ingredientes sin precio",
            "comparar receta interna con fuentes autorizadas o libros cargados",
            "proponer escandallo y alergenos para revision de cocina",
        ),
        "no_debe": (
            "inventar receta como si fuera oficial",
            "validar alergenos sin trazabilidad",
            "usar fuentes externas sin marcar procedencia",
        ),
        "datos": ("recetas", "recetas_ingredientes", "productos", "alergenos", "fuentes_recetas"),
    },
    "prl_appcc": {
        "respuesta_tipo": "control_normativo",
        "salida": "riesgos, formacion y evidencias pendientes",
        "puede": (
            "detectar formacion PRL o manipulador pendiente",
            "relacionar APPCC con zonas, equipos, temperaturas y tareas",
            "preparar documentacion y tareas de seguimiento",
        ),
        "no_debe": (
            "dar por impartida una formacion sin evidencia",
            "ocultar un bloqueo de seguridad alimentaria",
            "cerrar registros APPCC dudosos",
        ),
        "datos": ("empleados", "formacion", "appcc", "equipos", "registros_temperatura"),
    },
    "pms_overbooking": {
        "respuesta_tipo": "riesgo_recepcion",
        "salida": "riesgo, bloqueos y alternativas de alojamiento",
        "puede": (
            "cruzar reservas, habitaciones, cupos y canales",
            "avisar de riesgo de overbooking",
            "proponer bloqueo, desvio interno o hotel de apoyo",
        ),
        "no_debe": (
            "mover reservas confirmadas sin autorizacion",
            "abrir cupos sin control por canal",
            "mezclar centros de otra empresa",
        ),
        "datos": ("reservas", "habitaciones", "bloqueos", "canales", "empresas", "centros"),
    },
    "mantenimiento": {
        "respuesta_tipo": "orden_borrador",
        "salida": "orden de trabajo priorizada",
        "puede": (
            "crear propuesta de parte con ubicacion, prioridad y responsable",
            "relacionar averia con habitacion, activo o APPCC",
            "separar urgencia operativa de inversion futura",
        ),
        "no_debe": (
            "cerrar ordenes sin evidencia",
            "ocultar averias que bloquean venta o seguridad",
            "activar gasto extraordinario sin aprobacion",
        ),
        "datos": ("ordenes_trabajo", "activos", "habitaciones", "appcc", "presupuestos"),
    },
    "guest_service": {
        "respuesta_tipo": "diagnostico_experiencia",
        "salida": "lectura de satisfaccion y propuestas de atencion",
        "puede": (
            "cruzar quejas, reservas, repetidores y preferencias",
            "proponer atenciones o cambios operativos",
            "detectar patrones que afectan fidelizacion",
        ),
        "no_debe": (
            "prometer compensaciones sin autorizacion",
            "usar datos personales fuera del centro autorizado",
            "confundir una queja aislada con tendencia sin datos",
        ),
        "datos": ("reservas", "quejas", "clientes", "preferencias", "recomendaciones"),
    },
    "sala_preferencias": {
        "respuesta_tipo": "apoyo_servicio",
        "salida": "sugerencias de sala y preferencias",
        "puede": (
            "recordar preferencias utiles para mejorar servicio",
            "proponer venta sugerida o mise en place",
            "avisar de alergias o restricciones si estan registradas",
        ),
        "no_debe": (
            "inventar gustos de clientes",
            "mostrar datos sensibles a roles sin permiso",
            "cambiar reservas o cargos sin validacion",
        ),
        "datos": ("reservas", "clientes", "consumos", "alergenos", "puntos_venta"),
    },
    "inversion": {
        "respuesta_tipo": "analisis_estrategico",
        "salida": "escenario con riesgos, retorno y datos faltantes",
        "puede": (
            "comparar inversion con presupuesto, ocupacion y tendencia",
            "pedir datos externos cuando falten mercado o demanda",
            "proponer escenarios conservador, normal y tensionado",
        ),
        "no_debe": (
            "recomendar invertir sin sensibilidad de costes e ingresos",
            "confundir deseo comercial con capacidad real",
            "mezclar simulacion con resultado real",
        ),
        "datos": ("presupuestos", "ocupacion", "mantenimiento", "capex", "mercado"),
    },
}


def _normalizar(texto):
    return str(texto or "").strip().lower()


def _row_get(row, key, default=None):
    try:
        return row[key]
    except Exception:
        try:
            return dict(row).get(key, default)
        except Exception:
            return default


def _exists(conn, table):
    try:
        return conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None
    except Exception:
        return False


def _columns(conn, table):
    if not _exists(conn, table):
        return set()
    try:
        return {(_row_get(row, "name") or row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _scalar(conn, query, params=(), default=0):
    try:
        row = conn.execute(query, params).fetchone()
        if not row:
            return default
        return row[0] if row[0] is not None else default
    except Exception:
        return default


def _scope(cols, empresa_id=None, centro_id=None, alias=""):
    pref = f"{alias}." if alias else ""
    where = []
    params = []
    if empresa_id is not None:
        if "empresa_id" in cols:
            where.append(f"{pref}empresa_id=?")
            params.append(empresa_id)
        else:
            where.append("1=0")
    if centro_id is not None:
        if "centro_id" in cols:
            where.append(f"{pref}centro_id=?")
            params.append(centro_id)
        else:
            where.append("1=0")
    return where, params


def _count_table(conn, table, empresa_id=None, centro_id=None, extra_where=None, extra_params=()):
    cols = _columns(conn, table)
    if not cols:
        return 0
    where, params = _scope(cols, empresa_id, centro_id)
    if extra_where:
        where.append(extra_where)
        params.extend(extra_params)
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return int(_scalar(conn, sql, params, 0) or 0)


def _sum_table(conn, table, field, empresa_id=None, centro_id=None, extra_where=None, extra_params=()):
    cols = _columns(conn, table)
    if field not in cols:
        return 0.0
    where, params = _scope(cols, empresa_id, centro_id)
    if extra_where:
        where.append(extra_where)
        params.extend(extra_params)
    sql = f"SELECT COALESCE(SUM({field}),0) FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return float(_scalar(conn, sql, params, 0.0) or 0.0)


def _latest_rows(conn, table, empresa_id=None, centro_id=None, limit=5, fields="*"):
    cols = _columns(conn, table)
    if not cols:
        return []
    where, params = _scope(cols, empresa_id, centro_id)
    order = "id DESC" if "id" in cols else "rowid DESC"
    if "fecha_creacion" in cols:
        order = "datetime(fecha_creacion) DESC"
    elif "fecha" in cols:
        order = "date(fecha) DESC"
    sql = f"SELECT {fields} FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT ?"
    params.append(limit)
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def _count_recetas_sin_ingredientes(conn, empresa_id, centro_id=None):
    if not (_exists(conn, "recetas") and _exists(conn, "recetas_ingredientes")):
        return 0
    cols = _columns(conn, "recetas")
    where, params = _scope(cols, empresa_id, centro_id, "r")
    where.append("NOT EXISTS (SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id = r.id)")
    sql = "SELECT COUNT(*) FROM recetas r WHERE " + " AND ".join(where)
    return int(_scalar(conn, sql, params, 0) or 0)


def _count_recetas_con_cantidad_mal(conn, empresa_id, centro_id=None):
    if not (_exists(conn, "recetas") and _exists(conn, "recetas_ingredientes")):
        return 0
    cols = _columns(conn, "recetas")
    where, params = _scope(cols, empresa_id, centro_id, "r")
    where.append("EXISTS (SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id = r.id AND COALESCE(ri.cantidad,0)<=0)")
    sql = "SELECT COUNT(DISTINCT r.id) FROM recetas r WHERE " + " AND ".join(where)
    return int(_scalar(conn, sql, params, 0) or 0)


def _count_productos_sin_precio(conn, empresa_id, centro_id=None):
    return _count_table(conn, "productos", empresa_id, centro_id, "COALESCE(precio,0)<=0 AND COALESCE(activo,1)=1")


def _count_recetas_menu_sin_campo(conn, empresa_id, centro_id=None, campo="ingredientes"):
    if not (_exists(conn, "recetas") and _exists(conn, "menu_platos") and _exists(conn, "menu_subdepartamentos") and _exists(conn, "menus_diarios")):
        return 0
    md_cols = _columns(conn, "menus_diarios")
    where, params = _scope(md_cols, empresa_id, centro_id, "md")
    if campo == "alergenos":
        where.append("(r.alergenos IS NULL OR TRIM(r.alergenos)='')")
    else:
        if not _exists(conn, "recetas_ingredientes"):
            return 0
        where.append("NOT EXISTS (SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id = r.id)")
    sql = """
        SELECT COUNT(DISTINCT r.id)
        FROM recetas r
        JOIN menu_platos mp ON mp.receta_id = r.id
        JOIN menu_subdepartamentos ms ON ms.id = mp.menu_subdepartamento_id
        JOIN menus_diarios md ON md.id = ms.menu_diario_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    return int(_scalar(conn, sql, params, 0) or 0)


def _count_recetas_produccion_sin_campo(conn, empresa_id, centro_id=None, campo="ingredientes"):
    if not (_exists(conn, "recetas") and _exists(conn, "lineas_produccion") and _exists(conn, "partes_produccion")):
        return 0
    pp_cols = _columns(conn, "partes_produccion")
    where, params = _scope(pp_cols, empresa_id, centro_id, "pp")
    if campo == "alergenos":
        where.append("(r.alergenos IS NULL OR TRIM(r.alergenos)='')")
    else:
        if not _exists(conn, "recetas_ingredientes"):
            return 0
        where.append("NOT EXISTS (SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id = r.id)")
    sql = """
        SELECT COUNT(DISTINCT r.id)
        FROM recetas r
        JOIN lineas_produccion lp ON lp.receta_id = r.id
        JOIN partes_produccion pp ON pp.id = lp.parte_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    return int(_scalar(conn, sql, params, 0) or 0)


def _count_productos_usados_sin_precio(conn, empresa_id, centro_id=None):
    if not (_exists(conn, "productos") and _exists(conn, "recetas") and _exists(conn, "recetas_ingredientes")):
        return 0
    r_cols = _columns(conn, "recetas")
    where, params = _scope(r_cols, empresa_id, centro_id, "r")
    where.append("COALESCE(p.precio,0)<=0")
    where.append("COALESCE(p.activo,1)=1")
    sql = """
        SELECT COUNT(DISTINCT p.id)
        FROM productos p
        JOIN recetas_ingredientes ri ON ri.producto_id = p.id
        JOIN recetas r ON r.id = ri.receta_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    return int(_scalar(conn, sql, params, 0) or 0)


def _producto_tiene_proveedor_sql(conn):
    checks = []
    if _exists(conn, "productos_proveedores"):
        checks.append("""
            EXISTS (
                SELECT 1 FROM productos_proveedores pp
                WHERE pp.producto_id = p.id AND COALESCE(pp.activo,1)=1
            )
        """)
    if _exists(conn, "producto_proveedor"):
        checks.append("""
            EXISTS (
                SELECT 1 FROM producto_proveedor pp2
                WHERE pp2.producto_id = p.id AND COALESCE(pp2.activo,1)=1
            )
        """)
    if "proveedor_id" in _columns(conn, "productos"):
        checks.append("COALESCE(p.proveedor_id,0)>0")
    if not checks:
        return "0=1"
    return "(" + " OR ".join(checks) + ")"


def _count_productos_compras_sin_proveedor(conn, empresa_id, centro_id=None):
    if not _exists(conn, "productos"):
        return 0
    cols = _columns(conn, "productos")
    where, params = _scope(cols, empresa_id, centro_id, "p")
    where.append("COALESCE(p.activo,1)=1")
    if "visible_en_compras" in cols:
        where.append("COALESCE(p.visible_en_compras,1)=1")
    where.append(f"NOT {_producto_tiene_proveedor_sql(conn)}")
    sql = "SELECT COUNT(*) FROM productos p WHERE " + " AND ".join(where)
    return int(_scalar(conn, sql, params, 0) or 0)


def _count_pedidos_sin_lineas(conn, empresa_id, centro_id=None):
    if not (_exists(conn, "pedidos") and _exists(conn, "lineas_pedido")):
        return 0
    cols = _columns(conn, "pedidos")
    where, params = _scope(cols, empresa_id, centro_id, "p")
    where.append("COALESCE(p.estado,'') NOT IN ('cerrado','cerrada','recibido','recibida','cancelado','cancelada')")
    where.append("NOT EXISTS (SELECT 1 FROM lineas_pedido lp WHERE lp.pedido_id = p.id)")
    sql = "SELECT COUNT(*) FROM pedidos p WHERE " + " AND ".join(where)
    return int(_scalar(conn, sql, params, 0) or 0)


def _sum_pedidos_abiertos(conn, empresa_id, centro_id=None):
    if not (_exists(conn, "pedidos") and _exists(conn, "lineas_pedido")):
        return 0.0
    cols = _columns(conn, "pedidos")
    where, params = _scope(cols, empresa_id, centro_id, "p")
    where.append("COALESCE(p.estado,'') NOT IN ('cerrado','cerrada','recibido','recibida','cancelado','cancelada')")
    sql = """
        SELECT COALESCE(SUM(COALESCE(lp.cantidad_solicitada,0) * COALESCE(lp.precio_unitario,0)), 0)
        FROM pedidos p
        JOIN lineas_pedido lp ON lp.pedido_id = p.id
        WHERE """ + " AND ".join(where)
    return float(_scalar(conn, sql, params, 0) or 0)


def _sample_compras_prioritarias(conn, empresa_id, centro_id=None, limit=6):
    muestras = {"sin_precio": [], "sin_proveedor": [], "pedidos_sin_lineas": []}
    if _exists(conn, "productos"):
        cols = _columns(conn, "productos")
        where, params = _scope(cols, empresa_id, centro_id, "p")
        where.append("COALESCE(p.activo,1)=1")
        if "visible_en_compras" in cols:
            where.append("COALESCE(p.visible_en_compras,1)=1")
        base = " AND ".join(where)
        try:
            muestras["sin_precio"] = [dict(row) for row in conn.execute(f"""
                SELECT p.id, p.nombre, COALESCE(p.familia,'') AS familia, COALESCE(p.unidad,'') AS unidad
                FROM productos p
                WHERE {base} AND COALESCE(p.precio,0)<=0
                ORDER BY LOWER(p.familia), LOWER(p.nombre)
                LIMIT ?
            """, params + [limit]).fetchall()]
        except Exception:
            muestras["sin_precio"] = []
        try:
            muestras["sin_proveedor"] = [dict(row) for row in conn.execute(f"""
                SELECT p.id, p.nombre, COALESCE(p.familia,'') AS familia, COALESCE(p.unidad,'') AS unidad
                FROM productos p
                WHERE {base} AND NOT {_producto_tiene_proveedor_sql(conn)}
                ORDER BY LOWER(p.familia), LOWER(p.nombre)
                LIMIT ?
            """, params + [limit]).fetchall()]
        except Exception:
            muestras["sin_proveedor"] = []

    if _exists(conn, "pedidos") and _exists(conn, "lineas_pedido"):
        cols = _columns(conn, "pedidos")
        where, params = _scope(cols, empresa_id, centro_id, "p")
        where.append("COALESCE(p.estado,'') NOT IN ('cerrado','cerrada','recibido','recibida','cancelado','cancelada')")
        where.append("NOT EXISTS (SELECT 1 FROM lineas_pedido lp WHERE lp.pedido_id = p.id)")
        try:
            muestras["pedidos_sin_lineas"] = [dict(row) for row in conn.execute(f"""
                SELECT p.id, COALESCE(p.numero_pedido, '') AS numero_pedido, COALESCE(p.estado,'') AS estado,
                       COALESCE(p.fecha_pedido, '') AS fecha_pedido
                FROM pedidos p
                WHERE {" AND ".join(where)}
                ORDER BY p.id DESC
                LIMIT ?
            """, params + [limit]).fetchall()]
        except Exception:
            muestras["pedidos_sin_lineas"] = []
    return muestras


def _sample_recetas_prioritarias(conn, empresa_id, centro_id=None, limit=6):
    if not _exists(conn, "recetas"):
        return {"sin_ingredientes": [], "sin_alergenos": [], "productos_sin_precio": []}
    receta_scope = "r.empresa_id = ?"
    receta_params = [empresa_id]
    if centro_id is not None and "centro_id" in _columns(conn, "recetas"):
        receta_scope += " AND (r.centro_id = ? OR r.centro_id IS NULL)"
        receta_params.append(centro_id)

    def rows_recetas(extra_where, params_extra=()):
        params = [empresa_id, centro_id, centro_id, empresa_id, centro_id, centro_id] + receta_params + list(params_extra) + [limit]
        sql = f"""
            SELECT r.id, r.nombre, COALESCE(r.categoria, '') AS categoria,
                   COALESCE(r.alergenos, '') AS alergenos,
                   (SELECT COUNT(*) FROM recetas_ingredientes ri WHERE ri.receta_id = r.id) AS ingredientes,
                   (SELECT COUNT(*)
                    FROM menu_platos mp
                    JOIN menu_subdepartamentos ms ON ms.id = mp.menu_subdepartamento_id
                    JOIN menus_diarios md ON md.id = ms.menu_diario_id
                    WHERE mp.receta_id = r.id
                      AND md.empresa_id = ?
                      AND (? IS NULL OR md.centro_id = ?)) AS usos_menu,
                   (SELECT COUNT(*)
                    FROM lineas_produccion lp
                    JOIN partes_produccion pp ON pp.id = lp.parte_id
                    WHERE lp.receta_id = r.id
                      AND pp.empresa_id = ?
                      AND (? IS NULL OR pp.centro_id = ?)) AS usos_produccion
            FROM recetas r
            WHERE {receta_scope}
              AND {extra_where}
            ORDER BY (usos_menu + usos_produccion) DESC, usos_produccion DESC, usos_menu DESC, LOWER(r.nombre)
            LIMIT ?
        """
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        except Exception:
            return []

    sin_ingredientes = []
    if _exists(conn, "recetas_ingredientes") and _exists(conn, "menu_platos") and _exists(conn, "lineas_produccion"):
        sin_ingredientes = rows_recetas(
            "NOT EXISTS (SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id = r.id)"
        )

    sin_alergenos = []
    if _exists(conn, "menu_platos") and _exists(conn, "lineas_produccion"):
        sin_alergenos = rows_recetas("(r.alergenos IS NULL OR TRIM(r.alergenos) = '')")

    productos_sin_precio = []
    if _exists(conn, "productos") and _exists(conn, "recetas_ingredientes"):
        params = receta_params + [limit]
        sql = f"""
            SELECT DISTINCT p.id, p.nombre, COALESCE(p.unidad, '') AS unidad,
                   COUNT(DISTINCT r.id) AS recetas_afectadas
            FROM productos p
            JOIN recetas_ingredientes ri ON ri.producto_id = p.id
            JOIN recetas r ON r.id = ri.receta_id
            WHERE {receta_scope}
              AND COALESCE(p.precio, 0) <= 0
              AND COALESCE(p.activo, 1) = 1
            GROUP BY p.id
            ORDER BY recetas_afectadas DESC, LOWER(p.nombre)
            LIMIT ?
        """
        try:
            productos_sin_precio = [dict(row) for row in conn.execute(sql, params).fetchall()]
        except Exception:
            productos_sin_precio = []

    return {
        "sin_ingredientes": sin_ingredientes,
        "sin_alergenos": sin_alergenos,
        "productos_sin_precio": productos_sin_precio,
    }


def _count_empleados_sin_contrato(conn, empresa_id, centro_id=None):
    if not (_exists(conn, "empleados") and _exists(conn, "contratos")):
        return 0
    cols = _columns(conn, "empleados")
    where, params = _scope(cols, empresa_id, centro_id, "e")
    where.append("COALESCE(e.activo,1)=1")
    where.append(
        """
        NOT EXISTS (
            SELECT 1 FROM contratos c
            WHERE c.empleado_id = e.id
              AND COALESCE(c.estado,'activo') NOT IN ('baja','finalizado','finalizada','cancelado','cancelada')
        )
        """
    )
    sql = "SELECT COUNT(*) FROM empleados e WHERE " + " AND ".join(where)
    return int(_scalar(conn, sql, params, 0) or 0)


def _count_reservas_sin_habitacion(conn, empresa_id, centro_id=None):
    return _count_table(
        conn,
        "reservas_recepcion",
        empresa_id,
        centro_id,
        "COALESCE(estado,'confirmada') NOT IN ('cancelada','cancelado','no_show','no show') AND COALESCE(habitacion_id,0)=0",
    )


def _count_reservas_sin_precio(conn, empresa_id, centro_id=None):
    return _count_table(
        conn,
        "reservas_recepcion",
        empresa_id,
        centro_id,
        "COALESCE(estado,'confirmada') NOT IN ('cancelada','cancelado','no_show','no show') AND COALESCE(precio_total,0)<=0",
    )


def _diagnosticos_operativos(conn, empresa_id, centro_id=None):
    hoy = date.today()
    return {
        "rrhh": {
            "empleados_activos": _count_table(conn, "empleados", empresa_id, centro_id, "COALESCE(activo,1)=1"),
            "empleados_sin_contrato": _count_empleados_sin_contrato(conn, empresa_id, centro_id),
            "fichajes_mes": _count_table(conn, "fichajes", empresa_id, centro_id, "substr(fecha_hora,1,7)=?", (hoy.strftime("%Y-%m"),)),
            "nominas_mes": _count_table(conn, "nominas", empresa_id, centro_id, "mes=? AND anio=?", (hoy.month, hoy.year)) if _exists(conn, "nominas") else 0,
            "nominas_pendientes": _count_table(conn, "nominas", empresa_id, centro_id, "COALESCE(estado,'pendiente') NOT IN ('pagada','pagado','cerrada','cerrado')") if _exists(conn, "nominas") else 0,
        },
        "produccion": {
            "partes_abiertos": _count_table(conn, "partes_produccion", empresa_id, centro_id, "COALESCE(estado,'borrador') NOT IN ('cerrado','cerrada','finalizado','finalizada','cancelado','cancelada')"),
            "partes_mes": _count_table(conn, "partes_produccion", empresa_id, centro_id, "substr(fecha,1,7)=?", (hoy.strftime("%Y-%m"),)),
            "recetas_total": _count_table(conn, "recetas", empresa_id, centro_id),
            "recetas_sin_ingredientes": _count_recetas_sin_ingredientes(conn, empresa_id, centro_id),
            "recetas_con_cantidad_mal": _count_recetas_con_cantidad_mal(conn, empresa_id, centro_id),
            "productos_activos": _count_table(conn, "productos", empresa_id, centro_id, "COALESCE(activo,1)=1"),
            "productos_sin_precio": _count_productos_sin_precio(conn, empresa_id, centro_id),
            "pedidos_abiertos": _count_table(conn, "pedidos", empresa_id, centro_id, "COALESCE(estado,'') NOT IN ('cerrado','cerrada','recibido','recibida','cancelado','cancelada')"),
        },
        "recetas": {
            "catalogo_total": _count_table(conn, "recetas", empresa_id, centro_id),
            "datos_basicos_disponibles": (
                {"id", "alergenos"}.issubset(_columns(conn, "recetas"))
                and {"receta_id", "cantidad"}.issubset(_columns(conn, "recetas_ingredientes"))
                and {"id", "precio", "activo"}.issubset(_columns(conn, "productos"))
            ),
            "catalogo_sin_ingredientes": _count_recetas_sin_ingredientes(conn, empresa_id, centro_id),
            "catalogo_sin_alergenos": _count_table(conn, "recetas", empresa_id, centro_id, "(alergenos IS NULL OR TRIM(alergenos)='')") if _exists(conn, "recetas") else 0,
            "usadas_menu_sin_ingredientes": _count_recetas_menu_sin_campo(conn, empresa_id, centro_id, "ingredientes"),
            "usadas_produccion_sin_ingredientes": _count_recetas_produccion_sin_campo(conn, empresa_id, centro_id, "ingredientes"),
            "usadas_menu_sin_alergenos": _count_recetas_menu_sin_campo(conn, empresa_id, centro_id, "alergenos"),
            "usadas_produccion_sin_alergenos": _count_recetas_produccion_sin_campo(conn, empresa_id, centro_id, "alergenos"),
            "productos_usados_sin_precio": _count_productos_usados_sin_precio(conn, empresa_id, centro_id),
            "propuestas_fuente_pendientes": _count_table(conn, "recetas_fuentes_propuestas", empresa_id, centro_id, "COALESCE(estado,'pendiente') IN ('pendiente','revisar','pendiente_revision')") if _exists(conn, "recetas_fuentes_propuestas") else 0,
        },
        "menus": {
            "menus_7d": _count_table(conn, "menus_diarios", empresa_id, centro_id, "date(fecha) BETWEEN date(?) AND date(?)", (hoy.isoformat(), date.fromordinal(hoy.toordinal() + 7).isoformat())) if _exists(conn, "menus_diarios") else 0,
            "platos_menu": _count_table(conn, "menu_platos", empresa_id, None) if _exists(conn, "menu_platos") else 0,
            "tipos_menu": _count_table(conn, "tipos_menu", empresa_id, centro_id, "COALESCE(activo,1)=1") if _exists(conn, "tipos_menu") else 0,
        },
        "compras": {
            "datos_basicos_disponibles": (
                centro_id is not None
                and {"id", "empresa_id", "activo"}.issubset(_columns(conn, "proveedores"))
                and {"id", "empresa_id", "centro_id", "activo", "precio", "visible_en_compras"}.issubset(_columns(conn, "productos"))
                and {"id", "empresa_id", "centro_id", "estado"}.issubset(_columns(conn, "pedidos"))
                and {"pedido_id", "cantidad_solicitada", "precio_unitario"}.issubset(_columns(conn, "lineas_pedido"))
                and ("proveedor_id" in _columns(conn, "productos")
                     or {"producto_id", "activo"}.issubset(_columns(conn, "productos_proveedores"))
                     or {"producto_id", "activo"}.issubset(_columns(conn, "producto_proveedor")))
            ),
            "proveedores_activos": _count_table(conn, "proveedores", empresa_id, None, "COALESCE(activo,1)=1") if _exists(conn, "proveedores") else 0,
            "productos_comprables": _count_table(conn, "productos", empresa_id, centro_id, "COALESCE(activo,1)=1 AND COALESCE(visible_en_compras,1)=1") if _exists(conn, "productos") else 0,
            "productos_sin_precio": _count_productos_sin_precio(conn, empresa_id, centro_id),
            "productos_sin_proveedor": _count_productos_compras_sin_proveedor(conn, empresa_id, centro_id),
            "productos_con_proveedor": _count_table(conn, "producto_proveedor", empresa_id, None, "COALESCE(activo,1)=1") if _exists(conn, "producto_proveedor") else _count_table(conn, "productos_proveedores", empresa_id, None, "COALESCE(activo,1)=1") if _exists(conn, "productos_proveedores") else 0,
            "pedidos_abiertos": _count_table(conn, "pedidos", empresa_id, centro_id, "COALESCE(estado,'') NOT IN ('cerrado','cerrada','recibido','recibida','cancelado','cancelada')") if _exists(conn, "pedidos") else 0,
            "pedidos_sin_lineas": _count_pedidos_sin_lineas(conn, empresa_id, centro_id),
            "importe_pedidos_abiertos": _sum_pedidos_abiertos(conn, empresa_id, centro_id),
            "recepciones_pendientes_doc": _count_table(conn, "documentos_compra", empresa_id, None, "COALESCE(validado,0)=0") if _exists(conn, "documentos_compra") else 0,
        },
        "alergenos": {
            "recetas_sin_alergenos": _count_table(conn, "recetas", empresa_id, centro_id, "(alergenos IS NULL OR TRIM(alergenos)='')") if _exists(conn, "recetas") else 0,
            "propuestas_fuente_pendientes": _count_table(conn, "recetas_fuentes_propuestas", empresa_id, centro_id, "COALESCE(estado,'pendiente') IN ('pendiente','revisar','pendiente_revision')") if _exists(conn, "recetas_fuentes_propuestas") else 0,
            "clientes_con_alergias": _count_table(conn, "clientes_360", empresa_id, None, "COALESCE(alergias_resumen,'')<>''") if _exists(conn, "clientes_360") else 0,
        },
        "animacion": {
            "actividades_7d": _count_table(conn, "animacion_actividades", empresa_id, centro_id, "date(fecha) BETWEEN date(?) AND date(?)", (hoy.isoformat(), date.fromordinal(hoy.toordinal() + 7).isoformat())) if _exists(conn, "animacion_actividades") else 0,
            "actividades_pendientes": _count_table(conn, "animacion_actividades", empresa_id, centro_id, "COALESCE(estado,'programada') IN ('programada','pendiente','borrador')") if _exists(conn, "animacion_actividades") else 0,
            "asistencias_mes": _count_table(conn, "animacion_asistencias", empresa_id, None, "substr(fecha_creacion,1,7)=?", (hoy.strftime("%Y-%m"),)) if _exists(conn, "animacion_asistencias") else 0,
        },
        "appcc_prl": {
            "appcc_controles": _count_table(conn, "appcc_controles_plan", empresa_id, centro_id) if _exists(conn, "appcc_controles_plan") else 0,
            "appcc_no_ok_mes": _count_table(conn, "appcc_registros_plan", empresa_id, centro_id, "substr(fecha,1,7)=? AND COALESCE(resultado,'ok')<>'ok'", (hoy.strftime("%Y-%m"),)) if _exists(conn, "appcc_registros_plan") else 0,
            "prl_formaciones_pendientes": _count_table(conn, "prl_formacion_empleados", empresa_id, centro_id, "COALESCE(estado,'pendiente') NOT IN ('realizada','apta','completada')") if _exists(conn, "prl_formacion_empleados") else 0,
            "prl_incidentes_abiertos": _count_table(conn, "prl_incidentes", empresa_id, centro_id, "COALESCE(estado,'abierto') NOT IN ('cerrado','resuelto','descartado')") if _exists(conn, "prl_incidentes") else 0,
        },
        "tpv_sala": {
            "comandas_abiertas": _count_table(conn, "tpv_comandas", empresa_id, centro_id, "COALESCE(estado,'abierta') NOT IN ('cerrada','cancelada','cobrada')") if _exists(conn, "tpv_comandas") else 0,
            "cuentas_abiertas": _count_table(conn, "tpv_cuentas", empresa_id, centro_id, "COALESCE(estado,'abierta') NOT IN ('cerrada','cancelada','cobrada')") if _exists(conn, "tpv_cuentas") else 0,
            "cajas_abiertas": _count_table(conn, "tpv_caja_sesiones", empresa_id, centro_id, "COALESCE(estado,'abierta') NOT IN ('cerrada','cancelada')") if _exists(conn, "tpv_caja_sesiones") else 0,
            "articulos_visibles": _count_table(conn, "tpv_articulos", empresa_id, centro_id, "COALESCE(visible_tpv,1)=1") if _exists(conn, "tpv_articulos") else 0,
        },
        "clientes": {
            "clientes_360": _count_table(conn, "clientes_360", empresa_id, None) if _exists(conn, "clientes_360") else 0,
            "clientes_vip": _count_table(conn, "clientes_360", empresa_id, None, "COALESCE(vip_nivel,'')<>''") if _exists(conn, "clientes_360") else 0,
            "reservas_restaurante": _count_table(conn, "recepcion_reservas_restaurante", empresa_id, centro_id, "COALESCE(estado,'confirmada') NOT IN ('cancelada','cancelado')") if _exists(conn, "recepcion_reservas_restaurante") else 0,
        },
        "pms": {
            "reservas_activas": _count_table(conn, "reservas_recepcion", empresa_id, centro_id, "COALESCE(estado,'') NOT IN ('cancelada','cancelado','no_show','no show')"),
            "reservas_sin_habitacion": _count_reservas_sin_habitacion(conn, empresa_id, centro_id),
            "reservas_sin_precio": _count_reservas_sin_precio(conn, empresa_id, centro_id),
        },
        "finanzas": {
            "presupuesto_ingresos": _sum_table(conn, "contabilidad_presupuesto_lineas", "importe", empresa_id, centro_id, "tipo='ingreso'"),
            "presupuesto_gastos": _sum_table(conn, "contabilidad_presupuesto_lineas", "importe", empresa_id, centro_id, "tipo='gasto'"),
            "recurrentes": _count_table(conn, "contabilidad_recurrentes", empresa_id, centro_id, "COALESCE(activo,1)=1") if _exists(conn, "contabilidad_recurrentes") else 0,
            "documentos_pendientes_ia": _count_table(conn, "asistente_documentos_lectura", empresa_id, centro_id, "COALESCE(estado,'pendiente_revision') NOT IN ('validado','descartado')"),
            "facturas_pendientes": _count_table(conn, "contabilidad_documentos", empresa_id, centro_id, "COALESCE(estado,'pendiente') NOT IN ('pagada','cobrada','cerrada','cancelada')") if _exists(conn, "contabilidad_documentos") else 0,
            "recurrentes_activos": _count_table(conn, "contabilidad_recurrentes", empresa_id, centro_id, "COALESCE(activo,1)=1") if _exists(conn, "contabilidad_recurrentes") else 0,
        },
        "verifactu": {
            "estructura_preparada": 1 if _exists(conn, "verifactu_registros") and _exists(conn, "verifactu_config") else 0,
            "config_activa": _count_table(conn, "verifactu_config", empresa_id, centro_id, "COALESCE(activo,1)=1") if _exists(conn, "verifactu_config") else 0,
            "registros_mes": _count_table(conn, "verifactu_registros", empresa_id, centro_id, "substr(fecha_creacion,1,7)=?", (hoy.strftime("%Y-%m"),)) if _exists(conn, "verifactu_registros") else 0,
            "pendientes_envio": _count_table(conn, "verifactu_registros", empresa_id, centro_id, "COALESCE(estado_envio,'pendiente') IN ('pendiente','error','rechazado')") if _exists(conn, "verifactu_registros") else 0,
            "facturas_venta_mes": _count_table(conn, "recepcion_facturas", empresa_id, centro_id, "substr(fecha,1,7)=? AND COALESCE(tipo,'factura') <> 'proforma'", (hoy.strftime("%Y-%m"),)) if _exists(conn, "recepcion_facturas") else _count_table(conn, "contabilidad_documentos", empresa_id, centro_id, "substr(fecha_documento,1,7)=? AND tipo IN ('factura_venta','ticket_venta')", (hoy.strftime("%Y-%m"),)) if _exists(conn, "contabilidad_documentos") else 0,
        },
        "seguridad_social": {
            "estructura_preparada": 1 if _exists(conn, "rrhh_seguridad_social_ficheros") else 0,
            "config_prenomina": _count_table(conn, "configuracion_prenomina", empresa_id, None) if _exists(conn, "configuracion_prenomina") else 0,
            "ficheros_mes": _count_table(conn, "rrhh_seguridad_social_ficheros", empresa_id, None, "substr(fecha_creacion,1,7)=?", (hoy.strftime("%Y-%m"),)) if _exists(conn, "rrhh_seguridad_social_ficheros") else 0,
            "pendientes": _count_table(conn, "rrhh_seguridad_social_ficheros", empresa_id, None, "COALESCE(estado,'pendiente') IN ('pendiente','generado','preparado','enviado_sin_respuesta')") if _exists(conn, "rrhh_seguridad_social_ficheros") else 0,
            "errores": _count_table(conn, "rrhh_seguridad_social_ficheros", empresa_id, None, "COALESCE(estado,'pendiente') IN ('error','rechazado','rechazada','corregir')") if _exists(conn, "rrhh_seguridad_social_ficheros") else 0,
            "nominas_mes": _count_table(conn, "nominas", empresa_id, centro_id, "mes=? AND anio=?", (hoy.month, hoy.year)) if _exists(conn, "nominas") else 0,
            "prenominas_mes": _count_table(conn, "resumen_prenomina", empresa_id, centro_id, "mes=? AND anio=?", (hoy.month, hoy.year)) if _exists(conn, "resumen_prenomina") else 0,
        },
        "aprendizaje": {
            "recomendaciones_pendientes": _count_table(conn, "asistente_recomendaciones", empresa_id, None, "COALESCE(estado,'pendiente')='pendiente'") if _exists(conn, "asistente_recomendaciones") else 0,
            "recomendaciones_aceptadas": _count_table(conn, "asistente_recomendaciones", empresa_id, None, "COALESCE(estado,'pendiente') IN ('aceptada','validada','aplicada')") if _exists(conn, "asistente_recomendaciones") else 0,
            "recomendaciones_descartadas": _count_table(conn, "asistente_recomendaciones", empresa_id, None, "COALESCE(estado,'pendiente') IN ('descartada','rechazada')") if _exists(conn, "asistente_recomendaciones") else 0,
            "feedback_sin_resultado": _count_table(conn, "asistente_recomendaciones", empresa_id, None, "COALESCE(estado,'pendiente') NOT IN ('pendiente','borrador') AND COALESCE(resultado,'')=''") if _exists(conn, "asistente_recomendaciones") else 0,
            "eventos_mes": _count_table(conn, "asistente_eventos_aprendizaje", empresa_id, None, "substr(fecha_evento,1,7)=?", (hoy.strftime("%Y-%m"),)) if _exists(conn, "asistente_eventos_aprendizaje") else 0,
            "memorias_activas": _count_table(conn, "asistente_memoria", empresa_id, None, "COALESCE(estado,'activo')='activo'") if _exists(conn, "asistente_memoria") else 0,
        },
        "tareas": {
            "abiertas": _count_table(conn, "asistente_operativo_tareas", empresa_id, centro_id, "COALESCE(estado,'pendiente') IN ('pendiente','en_curso','bloqueada')") if _exists(conn, "asistente_operativo_tareas") else 0,
            "criticas": _count_table(conn, "asistente_operativo_tareas", empresa_id, centro_id, "COALESCE(estado,'pendiente') IN ('pendiente','en_curso','bloqueada') AND prioridad='critica'") if _exists(conn, "asistente_operativo_tareas") else 0,
            "bloqueadas": _count_table(conn, "asistente_operativo_tareas", empresa_id, centro_id, "COALESCE(estado,'pendiente')='bloqueada'") if _exists(conn, "asistente_operativo_tareas") else 0,
        },
        "senales": {
            "pendientes": _count_table(conn, "asistente_operativo_senales", empresa_id, centro_id, "COALESCE(estado,'pendiente')='pendiente'") if _exists(conn, "asistente_operativo_senales") else 0,
            "criticas": _count_table(conn, "asistente_operativo_senales", empresa_id, centro_id, "COALESCE(estado,'pendiente')='pendiente' AND severidad='critica'") if _exists(conn, "asistente_operativo_senales") else 0,
            "altas": _count_table(conn, "asistente_operativo_senales", empresa_id, centro_id, "COALESCE(estado,'pendiente')='pendiente' AND severidad='alta'") if _exists(conn, "asistente_operativo_senales") else 0,
        },
    }


def _objetivos_operativos(conn, empresa_id, centro_id=None, limit=8):
    if not _exists(conn, "asistente_operativo_objetivos"):
        return []
    cols = _columns(conn, "asistente_operativo_objetivos")
    where, params = _scope(cols, empresa_id, centro_id)
    where.append("COALESCE(estado,'activo')='activo'")
    sql = """
        SELECT modulo, objetivo, indicador, valor_objetivo, prioridad
        FROM asistente_operativo_objetivos
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += """
        ORDER BY CASE prioridad
            WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END,
            datetime(fecha_creacion) DESC
        LIMIT ?
    """
    params.append(limit)
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception:
        return []


def _tendencia_snapshots(conn, empresa_id, centro_id=None, limit=6):
    if not _exists(conn, "asistente_operativo_snapshots"):
        return {
            "estado": "sin_historico",
            "delta": 0,
            "lectura": "Aun no hay historico suficiente de salud operativa.",
            "puntos": [],
        }
    cols = _columns(conn, "asistente_operativo_snapshots")
    where, params = _scope(cols, empresa_id, centro_id)
    sql = """
        SELECT fecha_creacion, salud_score, estado, origen
        FROM asistente_operativo_snapshots
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY datetime(fecha_creacion) DESC, id DESC LIMIT ?"
    params.append(limit)
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        rows = []
    if len(rows) < 2:
        return {
            "estado": "sin_historico",
            "delta": 0,
            "lectura": "Guarda varias fotos operativas para medir tendencia.",
            "puntos": rows,
        }
    actual = int(rows[0].get("salud_score") or 0)
    anterior = int(rows[1].get("salud_score") or 0)
    delta = actual - anterior
    if delta >= 8:
        estado = "mejora"
        lectura = f"La salud operativa mejora {delta} puntos respecto a la foto anterior."
    elif delta <= -8:
        estado = "empeora"
        lectura = f"La salud operativa empeora {abs(delta)} puntos respecto a la foto anterior."
    elif actual < 60:
        estado = "estancada_baja"
        lectura = "La salud sigue baja y casi no se mueve; hay bloqueos que no se estan resolviendo."
    else:
        estado = "estable"
        lectura = "La salud se mantiene estable; conviene vigilar que las tareas no se acumulen."
    return {
        "estado": estado,
        "delta": delta,
        "lectura": lectura,
        "puntos": rows,
    }


def _ultima_foto_kpis(conn, empresa_id, centro_id=None):
    if not _exists(conn, "asistente_operativo_snapshots"):
        return {}
    cols = _columns(conn, "asistente_operativo_snapshots")
    if "kpis_json" not in cols:
        return {}
    where, params = _scope(cols, empresa_id, centro_id)
    sql = """
        SELECT kpis_json
        FROM asistente_operativo_snapshots
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY datetime(fecha_creacion) DESC, id DESC LIMIT 1"
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return {}
    raw = _row_get(row, "kpis_json") if row else ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return {item.get("clave"): item for item in (data.get("items") or []) if item.get("clave")}


def _aplicar_tendencia_kpis(conn, empresa_id, centro_id, kpis):
    anteriores = _ultima_foto_kpis(conn, empresa_id, centro_id)
    for kpi in kpis:
        previo = anteriores.get(kpi.get("clave"))
        actual = kpi.get("valor")
        anterior = previo.get("valor") if previo else None
        if previo is None or actual is None or anterior is None:
            kpi["tendencia"] = {
                "estado": "sin_historico",
                "delta": None,
                "lectura": "Sin foto anterior comparable.",
            }
            continue
        try:
            delta = round(float(actual) - float(anterior), 2)
        except Exception:
            kpi["tendencia"] = {
                "estado": "sin_historico",
                "delta": None,
                "lectura": "No se puede comparar este KPI.",
            }
            continue
        if delta >= 3:
            estado = "mejora"
            lectura = f"Mejora {delta:g}{kpi.get('unidad') or ''} desde la ultima foto."
        elif delta <= -3:
            estado = "empeora"
            lectura = f"Empeora {abs(delta):g}{kpi.get('unidad') or ''} desde la ultima foto."
        else:
            estado = "estable"
            lectura = "Se mantiene estable frente a la ultima foto."
        kpi["tendencia"] = {
            "estado": estado,
            "delta": delta,
            "lectura": lectura,
            "valor_anterior": anterior,
        }
    return kpis


def _ratio_ok(total, fallos):
    total = int(total or 0)
    fallos = int(fallos or 0)
    if total <= 0:
        return None
    return max(0, min(100, int(round(((total - fallos) / total) * 100))))


def _estado_kpi(valor, objetivo=100, invertido=False):
    if valor is None:
        return "sin_dato"
    valor = float(valor)
    objetivo = float(objetivo)
    if invertido:
        if valor <= objetivo:
            return "verde"
        if valor <= objetivo * 1.5:
            return "ambar"
        return "rojo"
    if valor >= objetivo:
        return "verde"
    if valor >= max(0, objetivo - 15):
        return "ambar"
    return "rojo"


def _kpi(clave, modulo, titulo, valor, unidad, objetivo, estado, lectura, accion, peso=1):
    return {
        "clave": clave,
        "modulo": modulo,
        "titulo": titulo,
        "valor": valor,
        "unidad": unidad,
        "objetivo": objetivo,
        "estado": estado,
        "lectura": lectura,
        "accion": accion,
        "peso": peso,
    }


def _kpis_operativos(contexto):
    ind = contexto.get("indicadores") or {}
    diag = contexto.get("diagnosticos") or {}
    rrhh = diag.get("rrhh") or {}
    prod = diag.get("produccion") or {}
    recetas = diag.get("recetas") or {}
    menus = diag.get("menus") or {}
    compras = diag.get("compras") or {}
    alergenos = diag.get("alergenos") or {}
    animacion = diag.get("animacion") or {}
    appcc_prl = diag.get("appcc_prl") or {}
    tpv_sala = diag.get("tpv_sala") or {}
    clientes = diag.get("clientes") or {}
    verifactu = diag.get("verifactu") or {}
    seguridad_social = diag.get("seguridad_social") or {}
    aprendizaje = diag.get("aprendizaje") or {}
    pms = diag.get("pms") or {}
    fin = diag.get("finanzas") or {}
    tareas = diag.get("tareas") or {}
    senales = diag.get("senales") or {}
    kpis = []

    valor = _ratio_ok(rrhh.get("empleados_activos"), rrhh.get("empleados_sin_contrato"))
    kpis.append(_kpi(
        "rrhh_contratos",
        "RRHH",
        "Contratos activos completos",
        valor,
        "%",
        100,
        _estado_kpi(valor, 100),
        "Mide si los empleados activos tienen contrato activo enlazado.",
        "Regularizar contratos antes de nominas y costes laborales.",
        5,
    ))

    valor = _ratio_ok(pms.get("reservas_activas"), pms.get("reservas_sin_habitacion"))
    kpis.append(_kpi(
        "pms_asignacion",
        "Recepcion/PMS",
        "Reservas con habitacion",
        valor,
        "%",
        100,
        _estado_kpi(valor, 100),
        "Mide si las reservas activas tienen habitacion asignada.",
        "Asignar habitaciones y revisar solapes antes de abrir cupos.",
        5,
    ))

    valor = _ratio_ok(pms.get("reservas_activas"), pms.get("reservas_sin_precio"))
    kpis.append(_kpi(
        "pms_tarifas",
        "Revenue/PMS",
        "Reservas con precio",
        valor,
        "%",
        100,
        _estado_kpi(valor, 100),
        "Mide si las reservas activas tienen tarifa e importe.",
        "Completar precio, regimen, canal y comision.",
        4,
    ))

    recetas_bloqueantes = int(recetas.get("usadas_menu_sin_ingredientes") or 0) + int(recetas.get("usadas_produccion_sin_ingredientes") or 0)
    recetas_fallos_kpi = max(int(prod.get("recetas_sin_ingredientes") or 0) // 4, recetas_bloqueantes * 4)
    valor = _ratio_ok(prod.get("recetas_total"), recetas_fallos_kpi)
    kpis.append(_kpi(
        "cocina_recetas",
        "Cocina",
        "Recetas calculables",
        valor,
        "%",
        95,
        _estado_kpi(valor, 95),
        "Mide si las recetas tienen ingredientes para compras y escandallos, priorizando las usadas en menu o produccion.",
        "Completar primero recetas usadas en menu/produccion; el catalogo pendiente va despues.",
        5,
    ))

    valor = _ratio_ok(prod.get("productos_activos"), prod.get("productos_sin_precio"))
    kpis.append(_kpi(
        "compras_precios",
        "Compras",
        "Productos con precio",
        valor,
        "%",
        95,
        _estado_kpi(valor, 95),
        "Mide si los productos activos tienen precio usable.",
        "Cargar precio estimado o proveedor principal para no falsear margen.",
        4,
    ))

    menus_ok = 100 if int(menus.get("menus_7d") or 0) > 0 else 0
    kpis.append(_kpi(
        "menus_planificacion",
        "Menus",
        "Menus proximos planificados",
        menus_ok,
        "%",
        100,
        _estado_kpi(menus_ok, 100),
        "Comprueba si hay menus diarios planificados para los proximos 7 dias.",
        "Planificar menus por servicio antes de compras, produccion y buffet.",
        4,
    ))

    alergenos_bloqueantes = int(recetas.get("usadas_menu_sin_alergenos") or 0) + int(recetas.get("usadas_produccion_sin_alergenos") or 0)
    alergenos_fallos_kpi = max(int(alergenos.get("recetas_sin_alergenos") or 0) // 4, alergenos_bloqueantes * 5)
    valor = _ratio_ok(prod.get("recetas_total"), alergenos_fallos_kpi)
    kpis.append(_kpi(
        "alergenos_recetas",
        "Alergenos",
        "Recetas con alergenos",
        valor,
        "%",
        98,
        _estado_kpi(valor, 98),
        "Mide si las recetas tienen alergenos informados.",
        "Revisar alergenos desde receta, productos y fuente validada antes de publicar cartas o buffet.",
        5,
    ))

    appcc_ok = max(0, 100 - int(appcc_prl.get("appcc_no_ok_mes") or 0) * 15 - int(appcc_prl.get("prl_incidentes_abiertos") or 0) * 20)
    kpis.append(_kpi(
        "appcc_prl_control",
        "APPCC/PRL",
        "Seguridad APPCC/PRL",
        appcc_ok,
        "%",
        90,
        _estado_kpi(appcc_ok, 90),
        "Cruza registros APPCC no OK, incidentes PRL y formacion pendiente.",
        "Cerrar no conformidades, evidencias y formacion obligatoria antes de asumir riesgo operativo.",
        5,
    ))

    animacion_ok = 100 if int(animacion.get("actividades_7d") or 0) > 0 or int(pms.get("reservas_activas") or 0) == 0 else 60
    kpis.append(_kpi(
        "animacion_programa",
        "Animacion",
        "Programa de animacion",
        animacion_ok,
        "%",
        80,
        _estado_kpi(animacion_ok, 80),
        "Mide si hay actividades proximas cuando hay ocupacion activa.",
        "Planificar actividades segun perfil de cliente, clima, edades y satisfaccion.",
        3,
    ))

    sala_ok = max(0, 100 - int(tpv_sala.get("comandas_abiertas") or 0) * 8 - int(tpv_sala.get("cajas_abiertas") or 0) * 15)
    kpis.append(_kpi(
        "tpv_sala_control",
        "TPV/Sala",
        "Sala y TPV controlados",
        sala_ok,
        "%",
        85,
        _estado_kpi(sala_ok, 85),
        "Mide comandas, cuentas y cajas abiertas.",
        "Cerrar cajas, revisar comandas abiertas y evitar cargos sin trazabilidad.",
        4,
    ))

    cliente_ok = 100 if int(clientes.get("clientes_360") or 0) > 0 else 70
    kpis.append(_kpi(
        "clientes_conocimiento",
        "Clientes",
        "Conocimiento cliente",
        cliente_ok,
        "%",
        85,
        _estado_kpi(cliente_ok, 85),
        "Mide si hay base de clientes 360 para preferencias, alergias, VIP y repetidores.",
        "Alimentar ficha 360 con preferencias, alergias y atenciones sin invadir privacidad.",
        3,
    ))

    facturas_venta_mes = int(verifactu.get("facturas_venta_mes") or 0)
    verifactu_ok = 100 if int(verifactu.get("estructura_preparada") or 0) else (70 if facturas_venta_mes == 0 else 20)
    kpis.append(_kpi(
        "verifactu_preparado",
        "Administracion",
        "Verifactu preparado",
        verifactu_ok,
        "%",
        100,
        _estado_kpi(verifactu_ok, 90),
        "Mide si facturacion esta preparada para registro verificable, QR, huella y trazabilidad.",
        "Crear estructura Verifactu, configurar modo, generar registros encadenados y dejar pruebas antes de obligatorio.",
        9,
    ))

    ss_base = 100 if int(seguridad_social.get("estructura_preparada") or 0) else 0
    if int(seguridad_social.get("errores") or 0) > 0:
        ss_ok = 20
    elif int(seguridad_social.get("pendientes") or 0) > 0:
        ss_ok = min(ss_base, 70)
    elif int(seguridad_social.get("nominas_mes") or 0) > 0 and int(seguridad_social.get("ficheros_mes") or 0) == 0:
        ss_ok = min(ss_base, 60)
    else:
        ss_ok = ss_base
    kpis.append(_kpi(
        "seguridad_social_preparada",
        "RRHH",
        "Seguridad Social preparada",
        ss_ok,
        "%",
        100,
        _estado_kpi(ss_ok, 90),
        "Mide si RRHH tiene estructura de ficheros, configuracion de prenomina y control de estados para Seguridad Social.",
        "Cruzar contratos, prenomina, nominas, bases, CRA/SILTRA/Sistema RED y dejar ficheros en borrador trazable antes de enviar.",
        9,
    ))

    aprendizaje_ok = max(
        0,
        100
        - int(aprendizaje.get("recomendaciones_pendientes") or 0) * 4
        - int(aprendizaje.get("feedback_sin_resultado") or 0) * 8,
    )
    kpis.append(_kpi(
        "aprendizaje_feedback",
        "Cerebro IA",
        "Aprendizaje con feedback",
        aprendizaje_ok,
        "%",
        85,
        _estado_kpi(aprendizaje_ok, 85),
        "Mide si las recomendaciones se cierran con resultado para que el cerebro aprenda de decisiones reales.",
        "Cerrar recomendaciones pendientes con aceptada, descartada, resultado y motivo; convertir lecciones utiles en memoria operativa.",
        6,
    ))

    recurrentes_ok = 100 if int(fin.get("recurrentes") or 0) > 0 else 0
    kpis.append(_kpi(
        "finanzas_recurrentes",
        "Finanzas",
        "Costes fijos configurados",
        recurrentes_ok,
        "%",
        100,
        _estado_kpi(recurrentes_ok, 100),
        "Comprueba si el centro tiene costes recurrentes activos.",
        "Crear luz, agua, gas, seguros, gestoria, internet e impuestos.",
        5,
    ))

    ingresos = float(ind.get("presupuesto_ingresos") or 0)
    resultado = float(ind.get("presupuesto_resultado") or 0)
    margen_pct = None if not ind.get("presupuesto_comparable") or ingresos <= 0 else round((resultado / ingresos) * 100, 1)
    estado_margen = "sin_dato"
    if margen_pct is not None:
        if margen_pct >= 10:
            estado_margen = "verde"
        elif margen_pct >= 0:
            estado_margen = "ambar"
        else:
            estado_margen = "rojo"
    kpis.append(_kpi(
        "finanzas_margen",
        "Finanzas",
        "Margen presupuestado",
        margen_pct,
        "%",
        10,
        estado_margen,
        "Sin dato fiable hasta seleccionar un presupuesto y periodo comparables.",
        "Seleccionar un presupuesto y revisar sus ingresos y costes antes de calcular el margen.",
        5,
    ))

    carga_pendiente = int(tareas.get("criticas") or 0) * 35 + int(tareas.get("bloqueadas") or 0) * 20 + int(tareas.get("abiertas") or 0) * 3
    valor = max(0, 100 - carga_pendiente)
    kpis.append(_kpi(
        "operacion_tareas",
        "Operacion",
        "Capacidad de cierre",
        valor,
        "%",
        80,
        _estado_kpi(valor, 80),
        "Mide si las tareas abiertas permiten operar sin bloqueo.",
        "Resolver criticas, desbloquear pendientes y asignar responsable.",
        4,
    ))

    carga_senales = int(senales.get("criticas") or 0) * 40 + int(senales.get("altas") or 0) * 18 + int(senales.get("pendientes") or 0) * 4
    valor = max(0, 100 - carga_senales)
    kpis.append(_kpi(
        "ia_inbox",
        "Inbox IA",
        "Senales procesadas",
        valor,
        "%",
        85,
        _estado_kpi(valor, 85),
        "Mide si las senales pendientes estan bajo control.",
        "Convertir senales criticas/altas en tareas o descartarlas con motivo.",
        4,
    ))

    orden = {"rojo": 0, "ambar": 1, "sin_dato": 2, "verde": 3}
    kpis.sort(key=lambda item: (orden.get(item["estado"], 4), -int(item.get("peso") or 0), item["titulo"]))
    resumen = {
        "total": len(kpis),
        "verdes": sum(1 for k in kpis if k["estado"] == "verde"),
        "ambar": sum(1 for k in kpis if k["estado"] == "ambar"),
        "rojos": sum(1 for k in kpis if k["estado"] == "rojo"),
        "sin_dato": sum(1 for k in kpis if k["estado"] == "sin_dato"),
    }
    resumen["lectura"] = (
        f"{resumen['rojos']} KPIs criticos, {resumen['ambar']} en vigilancia, "
        f"{resumen['verdes']} sanos y {resumen['sin_dato']} sin dato."
    )
    return {"resumen": resumen, "items": kpis}


def _mapa_nervioso(contexto):
    kpis = ((contexto.get("kpis") or {}).get("items") or [])
    diag = contexto.get("diagnosticos") or {}
    estado_peso = {"rojo": 0, "ambar": 1, "sin_dato": 2, "verde": 3}
    modulos = {}
    for kpi in kpis:
        modulo = kpi.get("modulo") or "Operacion"
        item = modulos.setdefault(modulo, {
            "modulo": modulo,
            "estado": "verde",
            "pulso": 100,
            "kpis": [],
            "alertas": 0,
            "lectura": "",
            "accion": "",
        })
        item["kpis"].append(kpi)
        if estado_peso.get(kpi.get("estado"), 9) < estado_peso.get(item["estado"], 9):
            item["estado"] = kpi.get("estado") or "sin_dato"
        if kpi.get("estado") in {"rojo", "ambar"}:
            item["alertas"] += 1

    for modulo, item in modulos.items():
        valores = [float(k["valor"]) for k in item["kpis"] if k.get("valor") is not None]
        item["pulso"] = int(round(sum(valores) / len(valores))) if valores else 0
        peor = next((k for k in item["kpis"] if k.get("estado") == "rojo"), None)
        if not peor:
            peor = next((k for k in item["kpis"] if k.get("estado") == "ambar"), None)
        if not peor:
            peor = item["kpis"][0] if item["kpis"] else None
        if peor:
            item["lectura"] = peor.get("lectura") or "Modulo sin lectura especifica."
            item["accion"] = peor.get("accion") or "Mantener seguimiento."

    # Enriquecer modulos que no tienen KPI directo pero si diagnostico operativo.
    extras = [
        ("Mantenimiento", int(((contexto.get("indicadores") or {}).get("ordenes_mantenimiento_abiertas")) or 0), "Ordenes de mantenimiento abiertas."),
        ("Tareas", int((diag.get("tareas") or {}).get("abiertas") or 0), "Tareas operativas abiertas."),
        ("Senales", int((diag.get("senales") or {}).get("pendientes") or 0), "Senales pendientes del inbox IA."),
    ]
    for modulo, cantidad, lectura in extras:
        if modulo in modulos:
            continue
        estado = "verde"
        if cantidad >= 10:
            estado = "rojo"
        elif cantidad > 0:
            estado = "ambar"
        modulos[modulo] = {
            "modulo": modulo,
            "estado": estado,
            "pulso": max(0, 100 - cantidad * 8),
            "kpis": [],
            "alertas": cantidad,
            "lectura": lectura,
            "accion": "Revisar, priorizar y cerrar lo que afecte cliente, seguridad o margen.",
        }

    items = list(modulos.values())
    items.sort(key=lambda item: (estado_peso.get(item["estado"], 9), item["pulso"], item["modulo"]))
    resumen = {
        "modulos": len(items),
        "rojos": sum(1 for item in items if item["estado"] == "rojo"),
        "ambar": sum(1 for item in items if item["estado"] == "ambar"),
        "verdes": sum(1 for item in items if item["estado"] == "verde"),
    }
    resumen["lectura"] = f"{resumen['rojos']} modulos criticos, {resumen['ambar']} en vigilancia y {resumen['verdes']} controlados."
    return {"resumen": resumen, "items": items}


def _simulador_contexto(conn, empresa_id, centro_id=None):
    data = {
        "activo": False,
        "centro": None,
        "resumen": {
            "centros_configurados": 0,
            "escenarios": 0,
            "corridas": 0,
            "resultados": 0,
            "criticos": 0,
            "altos": 0,
            "lectura": "No veo simulador configurado para el centro activo.",
        },
        "cartera": [],
        "ultimos_resultados": [],
        "prueba_fuego": None,
    }
    if not _exists(conn, "simulador_config_centros"):
        return data

    config_cols = _columns(conn, "simulador_config_centros")
    where, params = _scope(config_cols, empresa_id, None)
    sql = "SELECT * FROM simulador_config_centros"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(estado,'activo') DESC, id DESC"
    try:
        configs = [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception:
        configs = []
    data["resumen"]["centros_configurados"] = len(configs)

    for cfg in configs[:8]:
        cid = int(cfg.get("centro_id") or 0)
        escenarios = _count_table(conn, "simulador_escenarios", None, None, "config_id=?", (cfg.get("id"),)) if _exists(conn, "simulador_escenarios") else 0
        corridas = _count_table(conn, "simulador_corridas_generales", None, None, "config_id=?", (cfg.get("id"),)) if _exists(conn, "simulador_corridas_generales") else 0
        resultados = _count_table(conn, "simulador_resultados_generales", empresa_id, cid) if _exists(conn, "simulador_resultados_generales") else 0
        criticos = _count_table(conn, "simulador_resultados_generales", empresa_id, cid, "LOWER(COALESCE(severidad,'')) IN ('critica','critico','rojo')") if _exists(conn, "simulador_resultados_generales") else 0
        data["cartera"].append({
            "config_id": cfg.get("id"),
            "centro_id": cid,
            "nombre": cfg.get("nombre") or _nombre_centro(conn, cid),
            "tipo_negocio": cfg.get("tipo_negocio") or "",
            "estado": cfg.get("estado") or "",
            "nivel_riesgo": cfg.get("nivel_riesgo") or "",
            "db_laboratorio": cfg.get("db_laboratorio") or "",
            "escenarios": escenarios,
            "corridas": corridas,
            "resultados": resultados,
            "criticos": criticos,
        })

    actual = next((cfg for cfg in configs if int(cfg.get("centro_id") or 0) == int(centro_id or 0)), None)
    if not actual:
        data["resumen"]["lectura"] = "Hay simuladores configurados, pero no para el centro activo." if configs else data["resumen"]["lectura"]
        return data

    data["activo"] = True
    cid = int(actual.get("centro_id") or 0)
    data["centro"] = {
        "config_id": actual.get("id"),
        "centro_id": cid,
        "nombre": actual.get("nombre") or _nombre_centro(conn, cid),
        "tipo_negocio": actual.get("tipo_negocio") or "",
        "modo_aislamiento": actual.get("modo_aislamiento") or "",
        "db_laboratorio": actual.get("db_laboratorio") or "",
        "horizonte_defecto": actual.get("horizonte_defecto") or "",
        "estado": actual.get("estado") or "",
        "nivel_riesgo": actual.get("nivel_riesgo") or "",
        "permite_escritura_erp": actual.get("permite_escritura_erp"),
    }
    data["resumen"]["escenarios"] = _count_table(conn, "simulador_escenarios", None, None, "config_id=?", (actual.get("id"),)) if _exists(conn, "simulador_escenarios") else 0
    data["resumen"]["corridas"] = _count_table(conn, "simulador_corridas_generales", None, None, "config_id=?", (actual.get("id"),)) if _exists(conn, "simulador_corridas_generales") else 0
    data["resumen"]["resultados"] = _count_table(conn, "simulador_resultados_generales", empresa_id, cid) if _exists(conn, "simulador_resultados_generales") else 0
    data["resumen"]["criticos"] = _count_table(conn, "simulador_resultados_generales", empresa_id, cid, "LOWER(COALESCE(severidad,'')) IN ('critica','critico','rojo')") if _exists(conn, "simulador_resultados_generales") else 0
    data["resumen"]["altos"] = _count_table(conn, "simulador_resultados_generales", empresa_id, cid, "LOWER(COALESCE(severidad,'')) IN ('alta','alto','ambar')") if _exists(conn, "simulador_resultados_generales") else 0

    if _exists(conn, "simulador_resultados_generales"):
        try:
            data["ultimos_resultados"] = [dict(row) for row in conn.execute(
                """
                SELECT fecha, modulo, indicador, valor_num, valor_texto, severidad, recomendacion
                FROM simulador_resultados_generales
                WHERE empresa_id=? AND centro_id=?
                ORDER BY date(fecha) DESC, id DESC
                LIMIT 8
                """,
                (empresa_id, cid),
            ).fetchall()]
        except Exception:
            data["ultimos_resultados"] = []

    if _exists(conn, "simulador_prueba_fuego_centros"):
        try:
            row = conn.execute(
                """
                SELECT fecha, year, score, estado, resumen_json
                FROM simulador_prueba_fuego_centros
                WHERE empresa_id=? AND centro_id=?
                ORDER BY datetime(fecha) DESC, id DESC
                LIMIT 1
                """,
                (empresa_id, cid),
            ).fetchone()
            data["prueba_fuego"] = dict(row) if row else None
        except Exception:
            data["prueba_fuego"] = None

    if data["resumen"]["criticos"]:
        data["resumen"]["lectura"] = f"El simulador del centro tiene {data['resumen']['criticos']} resultados criticos pendientes de revisar."
    elif data["resumen"]["altos"]:
        data["resumen"]["lectura"] = f"El simulador del centro tiene {data['resumen']['altos']} resultados altos en vigilancia."
    elif data["resumen"]["corridas"]:
        data["resumen"]["lectura"] = "El simulador del centro tiene corridas registradas sin alertas fuertes."
    else:
        data["resumen"]["lectura"] = "El centro tiene simulador configurado, pero aun faltan corridas o resultados."
    return data


def _lectura_copiloto(contexto):
    brief = contexto.get("brief") or {}
    mapa = contexto.get("mapa_nervioso") or {}
    simulacion = contexto.get("simulacion") or {}
    ind = contexto.get("indicadores") or {}
    kpis = (contexto.get("kpis") or {}).get("items") or []
    nervios = mapa.get("items") or []
    criticos = [item for item in nervios if item.get("estado") == "rojo"]
    vigilancia = [item for item in nervios if item.get("estado") == "ambar"]
    kpis_empeoran = [item for item in kpis if (item.get("tendencia") or {}).get("estado") == "empeora"]

    foco = criticos[0] if criticos else vigilancia[0] if vigilancia else nervios[0] if nervios else None
    if foco:
        titular = f"Miraria primero {foco.get('modulo')}."
        motivo = foco.get("lectura") or "Es el area con peor pulso operativo ahora mismo."
        accion = foco.get("accion") or "Revisar el modulo y crear tarea si hay riesgo real."
    else:
        titular = "No veo un foco operativo principal."
        motivo = brief.get("lectura") or "Faltan datos para una lectura clara."
        accion = "Guardar una foto operativa y volver a escanear."

    avisos = []
    if criticos:
        avisos.append(f"{len(criticos)} modulos criticos.")
    if vigilancia:
        avisos.append(f"{len(vigilancia)} modulos en vigilancia.")
    if kpis_empeoran:
        avisos.append(f"{len(kpis_empeoran)} KPIs empeoran desde la ultima foto.")
    sim_resumen = simulacion.get("resumen") or {}
    if sim_resumen.get("criticos", 0) > 0:
        avisos.append(f"Simulador: {sim_resumen['criticos']} resultados criticos.")
    elif sim_resumen.get("altos", 0) > 0:
        avisos.append(f"Simulador: {sim_resumen['altos']} resultados altos.")
    if not avisos:
        avisos.append("Sin avisos fuertes; mantener seguimiento.")

    pros = []
    contras = []
    riesgos = list((brief.get("riesgos") or [])[:3])
    mejoras = []
    recortes = []

    verdes = [item for item in nervios if item.get("estado") == "verde"]
    if verdes:
        pros.append(f"{len(verdes)} areas aparecen controladas.")
    if (mapa.get("resumen") or {}).get("rojos", 0) == 0:
        pros.append("No hay modulos criticos en el mapa nervioso.")
    if ind.get("presupuesto_comparable") and float(ind.get("presupuesto_resultado") or 0) >= 0 and float(ind.get("presupuesto_ingresos") or 0) > 0:
        pros.append("El presupuesto no aparece en negativo en la lectura rapida.")
    if not pros:
        pros.append("El sistema al menos ya separa foco, causa y siguiente paso.")

    if criticos:
        contras.append(f"{len(criticos)} areas requieren atencion inmediata.")
    if vigilancia:
        contras.append(f"{len(vigilancia)} areas estan en vigilancia.")
    if any(k.get("estado") == "sin_dato" for k in kpis):
        contras.append("Hay KPIs sin dato fiable.")
    if not contras:
        contras.append("Sin contras fuertes detectadas en esta lectura.")

    for item in (criticos + vigilancia)[:3]:
        mejoras.append(item.get("accion") or f"Revisar {item.get('modulo')}.")
    if not mejoras:
        mejoras.append("Mantener seguimiento, guardar foto operativa y comparar tendencia.")

    if ind.get("presupuesto_comparable") and float(ind.get("presupuesto_resultado") or 0) < 0:
        recortes.append("Antes de recortar, separar perdida por precio, ocupacion, laboral, cocina y fijos.")
    recortes.append("Recortar solo donde no baje satisfaccion, seguridad, APPCC ni capacidad de venta.")
    if any((k.get("clave") == "compras_precios" and k.get("estado") in {"rojo", "ambar"}) for k in kpis):
        recortes.append("No negociar compras a ciegas: primero completar precios y proveedores fiables.")

    return {
        "titular": titular,
        "motivo": motivo,
        "accion": accion,
        "avisos": avisos[:4],
        "modo": "critico" if criticos else "vigilancia" if vigilancia else "control",
        "pros": pros[:4],
        "contras": contras[:4],
        "riesgos": riesgos[:4],
        "mejoras": mejoras[:4],
        "recortes": recortes[:4],
    }


def _herramientas_disponibles(conn):
    herramientas = []
    for item in HERRAMIENTAS_OPERATIVAS:
        requeridas = list(item.get("requiere") or [])
        existentes = [t for t in requeridas if _exists(conn, t)]
        faltantes = [t for t in requeridas if t not in existentes]
        copia = dict(item)
        copia["tablas_existentes"] = existentes
        copia["tablas_faltantes"] = faltantes
        copia["disponible_pct"] = int(round((len(existentes) / max(1, len(requeridas))) * 100))
        if faltantes:
            copia["estado_real"] = "parcial"
        elif item.get("estado") == "borrador":
            copia["estado_real"] = "prepara_borrador"
        else:
            copia["estado_real"] = "lectura"
        herramientas.append(copia)
    return herramientas


def _modularidad_operativa(herramientas):
    herramientas = herramientas or []
    total = len(herramientas)
    completas = [h for h in herramientas if not h.get("tablas_faltantes")]
    parciales = [h for h in herramientas if h.get("tablas_faltantes") and h.get("tablas_existentes")]
    ausentes = [h for h in herramientas if h.get("tablas_faltantes") and not h.get("tablas_existentes")]
    score = int(round((len(completas) / total) * 100)) if total else 0
    if score >= 85:
        estado = "preparado"
        lectura = "La instalacion tiene la mayoria de modulos operativos conectados al cerebro."
    elif score >= 50:
        estado = "parcial"
        lectura = "La instalacion puede trabajar por modulos, pero hay piezas incompletas que limitan recomendaciones."
    else:
        estado = "basico"
        lectura = "La instalacion funciona como nucleo basico; el cerebro debe responder con prudencia por falta de modulos."
    return {
        "score": score,
        "estado": estado,
        "lectura": lectura,
        "total": total,
        "completas": len(completas),
        "parciales": len(parciales),
        "ausentes": len(ausentes),
        "modulos_completos": [h.get("modulo") for h in completas],
        "modulos_parciales": [h.get("modulo") for h in parciales],
        "modulos_ausentes": [h.get("modulo") for h in ausentes],
    }


def _herramientas_para_intencion(contexto, intencion_clave):
    return [
        h for h in (contexto.get("herramientas") or [])
        if h.get("intencion") == intencion_clave
    ]


def _plan_operativo(intencion_clave, diagnosticos):
    rrhh = diagnosticos.get("rrhh") or {}
    prod = diagnosticos.get("produccion") or {}
    pms = diagnosticos.get("pms") or {}
    fin = diagnosticos.get("finanzas") or {}
    planes = {
        "nominas_borrador": [
            "Separar empleados activos por centro y departamento.",
            f"Bloquear calculo de {rrhh.get('empleados_sin_contrato', 0)} empleados sin contrato activo.",
            "Cruzar fichajes, ausencias, variables, embargos y anticipos.",
            "Generar prenomina en borrador y marcar incidencias.",
            "Dejar pago y cierre de nomina pendiente de validacion humana.",
        ],
        "pedidos_produccion": [
            "Leer menus y partes de produccion previstos.",
            f"Bloquear recetas sin ingredientes: {prod.get('recetas_sin_ingredientes', 0)}.",
            f"Bloquear productos sin precio: {prod.get('productos_sin_precio', 0)}.",
            "Calcular necesidades por producto, descontar stock util y agrupar por proveedor.",
            "Crear pedidos en borrador con fecha limite y riesgo APPCC.",
        ],
        "pms_overbooking": [
            "Leer reservas activas, fechas, regimenes, canales y habitaciones.",
            f"Resolver reservas sin habitacion: {pms.get('reservas_sin_habitacion', 0)}.",
            f"Revisar reservas sin precio: {pms.get('reservas_sin_precio', 0)}.",
            "Detectar solapes, bloqueos por canal y riesgo de overbooking.",
            "Proponer ajuste de cupos, desvio interno o hotel de apoyo si no hay capacidad.",
        ],
        "factura_documento": [
            "Leer documento subido y extraer proveedor, fecha, base, IVA, total y concepto.",
            "Buscar pedido, albaran o reserva relacionada.",
            "Marcar incoherencias entre pedido, recepcion y factura.",
            "Preparar documento administrativo/contable en borrador.",
            "Esperar validacion antes de tocar contabilidad oficial o stock definitivo.",
        ],
        "receta_coste": [
            "Leer receta, ingredientes, cantidades, alergenos y precio de productos.",
            f"Priorizar recetas sin ingredientes: {prod.get('recetas_sin_ingredientes', 0)}.",
            f"Priorizar recetas con cantidades mal: {prod.get('recetas_con_cantidad_mal', 0)}.",
            "Calcular coste teorico, coste real si hay produccion y desviacion.",
            "Proponer ajuste sin bajar seguridad alimentaria ni satisfaccion.",
        ],
        "revisar_costes": [
            "Separar ingresos, gastos, coste laboral, recurrentes, produccion y mantenimiento.",
            f"Verificar recurrentes configurados: {fin.get('recurrentes', 0)}.",
            "Comparar presupuesto contra datos reales y simulados.",
            "Detectar si el problema viene de precio, ocupacion, personal, compras o merma.",
            "Proponer una accion medible y un indicador para comprobar si funciona.",
        ],
        "mantenimiento": [
            "Clasificar averia por ubicacion, urgencia, APPCC, cliente y coste.",
            "Comprobar si existe orden abierta similar.",
            "Preparar orden con responsable, prioridad y fecha objetivo.",
            "Si afecta venta o seguridad, escalar a direccion.",
            "Cerrar solo con evidencia y coste imputado.",
        ],
        "prl_appcc": [
            "Comprobar trabajadores activos y formacion obligatoria.",
            "Detectar PRL, manipulador o APPCC pendiente/caducado.",
            "Proponer clase/documento y registro de asistencia.",
            "Guardar evidencia y bloquear tareas sensibles si falta formacion critica.",
        ],
        "guest_service": [
            "Leer reservas entrantes, repetidores, quejas y preferencias.",
            "Detectar clientes sensibles o de alto valor.",
            "Proponer detalles de bajo coste y alto impacto.",
            "Avisar a recepcion, pisos, sala o cocina segun necesidad.",
            "Medir resultado con satisfaccion, repeticion o queja resuelta.",
        ],
    }
    return planes.get(intencion_clave, [
        "Leer datos internos del centro activo.",
        "Separar dato real, supuesto y hueco.",
        "Proponer accion concreta.",
        "Pedir confirmacion si afecta dinero, stock, reservas, empleados o legal.",
    ])


def _preguntas_siguientes(intencion_clave, diagnosticos, contexto):
    rrhh = diagnosticos.get("rrhh") or {}
    prod = diagnosticos.get("produccion") or {}
    pms = diagnosticos.get("pms") or {}
    fin = diagnosticos.get("finanzas") or {}
    preguntas = []
    if intencion_clave == "nominas_borrador":
        if rrhh.get("empleados_sin_contrato", 0) > 0:
            preguntas.append("Confirmame si bloqueo empleados sin contrato o creo contrato provisional para revision.")
        if rrhh.get("fichajes_mes", 0) == 0:
            preguntas.append("No veo fichajes del mes: quieres calcular solo con contrato o paramos nominas?")
        preguntas.append("Que periodo quieres preparar: mes actual, mes anterior o fechas concretas?")
    elif intencion_clave == "pedidos_produccion":
        if prod.get("recetas_sin_ingredientes", 0) > 0:
            preguntas.append("Quieres que priorice completar recetas usadas en menu antes de generar pedidos?")
        if prod.get("productos_sin_precio", 0) > 0:
            preguntas.append("Uso precios estimados para productos sin precio o bloqueo esos productos?")
        preguntas.append("Para cuantos dias preparo necesidades: 3, 7 o temporada completa?")
    elif intencion_clave == "pms_overbooking":
        if pms.get("reservas_sin_habitacion", 0) > 0:
            preguntas.append("Quieres asignacion automatica de habitaciones en borrador antes de revisar overbooking?")
        if pms.get("reservas_sin_precio", 0) > 0:
            preguntas.append("Bloqueo reservas sin precio para revenue o las trato como operativas?")
        preguntas.append("Reviso solo el centro activo o tambien hoteles de apoyo de la empresa?")
    elif intencion_clave == "revisar_costes":
        if fin.get("recurrentes", 0) == 0:
            preguntas.append("Faltan recurrentes: quieres crear plantilla de luz, agua, gas, seguros, gestoria e impuestos?")
        if prod.get("productos_sin_precio", 0) > 0:
            preguntas.append("Quieres que el coste de cocina use precios estimados hasta validar proveedores?")
        preguntas.append("Buscamos salvar margen este mes o preparar presupuesto anual?")
    elif intencion_clave == "factura_documento":
        preguntas.extend([
            "El documento afecta compras, contabilidad, recepcion o TPV?",
            "Quieres que busque pedido/albaran relacionado antes de preparar la propuesta?",
        ])
    elif intencion_clave == "receta_coste":
        preguntas.extend([
            "Quieres mejorar coste manteniendo receta o reconstruir la ficha desde fuente externa/libros?",
            "La receta se usara para buffet, carta, menu del dia o produccion interna?",
        ])
    elif intencion_clave in {"mantenimiento", "prl_appcc", "guest_service"}:
        preguntas.append("Quieres que lo deje como aviso, orden/tarea o recomendacion para direccion?")
    if not preguntas:
        preguntas.append("Quieres que prepare borrador o solo analisis?")
    return preguntas[:3]


def _brief_operativo(contexto):
    ind = contexto.get("indicadores") or {}
    diag = contexto.get("diagnosticos") or {}
    rrhh = diag.get("rrhh") or {}
    prod = diag.get("produccion") or {}
    pms = diag.get("pms") or {}
    fin = diag.get("finanzas") or {}
    tareas = diag.get("tareas") or {}
    senales = diag.get("senales") or {}
    kpis = (contexto.get("kpis") or {}).get("items") or []
    tendencia = contexto.get("tendencia") or {}
    riesgos = []
    acciones = []
    causas = []
    score = 100

    if rrhh.get("empleados_activos", 0) <= 0:
        score -= 18
        riesgos.append("No hay empleados activos enlazados al centro.")
        acciones.append("Revisar RRHH y centro de trabajo antes de confiar en costes laborales.")
        causas.append({"modulo": "RRHH", "peso": 18, "motivo": "Sin empleados activos enlazados.", "accion": "Enlazar empleados al centro o revisar centro activo."})
    if rrhh.get("empleados_sin_contrato", 0) > 0:
        peso = min(18, rrhh["empleados_sin_contrato"] * 2)
        score -= peso
        riesgos.append(f"{rrhh['empleados_sin_contrato']} empleados activos sin contrato activo.")
        acciones.append("Bloquear nominas de empleados sin contrato hasta revisar datos.")
        causas.append({"modulo": "RRHH", "peso": peso, "motivo": "Empleados activos sin contrato.", "accion": "Crear o regularizar contratos antes de calcular nominas."})
    if pms.get("reservas_sin_habitacion", 0) > 0:
        peso = min(20, pms["reservas_sin_habitacion"] * 2)
        score -= peso
        riesgos.append(f"{pms['reservas_sin_habitacion']} reservas activas sin habitacion asignada.")
        acciones.append("Asignar habitaciones en borrador y revisar solapes antes de abrir cupos.")
        causas.append({"modulo": "Recepcion/PMS", "peso": peso, "motivo": "Reservas activas sin habitacion.", "accion": "Asignar habitaciones en borrador y revisar solapes."})
    if pms.get("reservas_sin_precio", 0) > 0:
        peso = min(12, pms["reservas_sin_precio"])
        score -= peso
        riesgos.append(f"{pms['reservas_sin_precio']} reservas activas sin precio.")
        acciones.append("Revisar revenue: reserva sin precio distorsiona margen y presupuesto.")
        causas.append({"modulo": "Revenue/PMS", "peso": peso, "motivo": "Reservas sin precio.", "accion": "Completar tarifa, regimen, canal y comision."})
    if prod.get("recetas_sin_ingredientes", 0) > 0:
        peso = min(18, prod["recetas_sin_ingredientes"])
        score -= peso
        riesgos.append(f"{prod['recetas_sin_ingredientes']} recetas sin ingredientes.")
        acciones.append("Completar recetas usadas en menu antes de generar compras o escandallos.")
        causas.append({"modulo": "Cocina", "peso": peso, "motivo": "Recetas sin ingredientes.", "accion": "Completar ingredientes y alergenos de recetas usadas en menu."})
    if prod.get("productos_sin_precio", 0) > 0:
        peso = min(15, prod["productos_sin_precio"] // 3 + 1)
        score -= peso
        riesgos.append(f"{prod['productos_sin_precio']} productos sin precio.")
        acciones.append("Cargar precios estimados o bloquear costes de cocina hasta validar proveedor.")
        causas.append({"modulo": "Compras/Cocina", "peso": peso, "motivo": "Productos sin precio.", "accion": "Cargar precio estimado o proveedor principal."})
    if fin.get("recurrentes", 0) <= 0:
        score -= 10
        riesgos.append("No hay costes recurrentes configurados.")
        acciones.append("Crear recurrentes de luz, agua, gas, seguros, gestoria, internet e impuestos.")
        causas.append({"modulo": "Finanzas", "peso": 10, "motivo": "Costes recurrentes no configurados.", "accion": "Crear recurrentes mensuales y anuales del centro."})
    if ind.get("presupuesto_comparable") and float(ind.get("presupuesto_resultado") or 0) < 0:
        score -= 14
        riesgos.append(f"Presupuesto con resultado negativo: {_format_euros(ind.get('presupuesto_resultado'))}.")
        acciones.append("Separar problema entre precio, ocupacion, coste laboral, cocina y costes fijos.")
        causas.append({"modulo": "Finanzas", "peso": 14, "motivo": "Presupuesto con resultado negativo.", "accion": "Separar perdida por ingresos, laboral, cocina, fijos y mantenimiento."})
    if tareas.get("criticas", 0) > 0:
        peso = min(15, tareas["criticas"] * 5)
        score -= peso
        riesgos.append(f"{tareas['criticas']} tareas criticas abiertas.")
        acciones.append("Resolver o reasignar tareas criticas antes de abrir nuevas decisiones.")
        causas.append({"modulo": "Operacion", "peso": peso, "motivo": "Tareas criticas abiertas.", "accion": "Resolver, reasignar o desbloquear tareas criticas."})
    if tareas.get("bloqueadas", 0) > 0:
        peso = min(12, tareas["bloqueadas"] * 3)
        score -= peso
        riesgos.append(f"{tareas['bloqueadas']} tareas bloqueadas.")
        acciones.append("Revisar bloqueos: puede faltar dato, responsable, presupuesto o capacidad real.")
        causas.append({"modulo": "Operacion", "peso": peso, "motivo": "Tareas bloqueadas.", "accion": "Identificar si falta dato, responsable, presupuesto o capacidad."})
    if senales.get("criticas", 0) > 0:
        peso = min(16, senales["criticas"] * 8)
        score -= peso
        riesgos.append(f"{senales['criticas']} senales criticas pendientes en el inbox operativo.")
        acciones.append("Procesar senales criticas y convertirlas en tareas o recomendaciones.")
        causas.append({"modulo": "Inbox IA", "peso": peso, "motivo": "Senales criticas pendientes.", "accion": "Procesar inbox operativo y asignar responsable."})
    elif senales.get("altas", 0) > 0:
        peso = min(10, senales["altas"] * 4)
        score -= peso
        riesgos.append(f"{senales['altas']} senales altas pendientes en el inbox operativo.")
        acciones.append("Procesar senales altas antes de que se conviertan en incidencias.")
        causas.append({"modulo": "Inbox IA", "peso": peso, "motivo": "Senales altas pendientes.", "accion": "Revisar inbox operativo."})
    for kpi in [k for k in kpis if k.get("estado") == "rojo"][:3]:
        peso = int(kpi.get("peso") or 1) * 2
        score -= peso
        riesgos.append(f"KPI critico: {kpi.get('titulo')} en {kpi.get('modulo')}.")
        acciones.append(kpi.get("accion") or "Revisar KPI critico.")
        causas.append({
            "modulo": kpi.get("modulo") or "KPI",
            "peso": peso,
            "motivo": f"KPI critico: {kpi.get('titulo')}",
            "accion": kpi.get("accion") or "Revisar KPI critico.",
        })
    for kpi in [k for k in kpis if (k.get("tendencia") or {}).get("estado") == "empeora"][:2]:
        peso = max(2, int(kpi.get("peso") or 1))
        score -= peso
        riesgos.append(f"KPI empeora: {kpi.get('titulo')} en {kpi.get('modulo')}.")
        acciones.append(kpi.get("accion") or "Revisar KPI que empeora.")
        causas.append({
            "modulo": kpi.get("modulo") or "KPI",
            "peso": peso,
            "motivo": f"KPI en deterioro: {kpi.get('titulo')}",
            "accion": kpi.get("accion") or "Revisar KPI que empeora.",
        })
    if tendencia.get("estado") == "empeora":
        score -= 8
        riesgos.append(tendencia.get("lectura") or "La tendencia operativa empeora.")
        acciones.append("Comparar las dos ultimas fotos operativas y atacar la causa del deterioro.")
        causas.append({"modulo": "Direccion", "peso": 8, "motivo": "Tendencia operativa empeora.", "accion": "Comparar fotos operativas y cerrar la causa principal."})
    elif tendencia.get("estado") == "estancada_baja":
        score -= 6
        riesgos.append(tendencia.get("lectura") or "La salud operativa esta estancada en nivel bajo.")
        acciones.append("No crear mas planes: cerrar bloqueos pendientes y medir de nuevo.")
        causas.append({"modulo": "Direccion", "peso": 6, "motivo": "Tendencia baja estancada.", "accion": "No abrir nuevas iniciativas hasta desbloquear pendientes."})

    score = max(0, min(100, int(score)))
    if score >= 80:
        estado = "verde"
        lectura = "El centro parece operativamente controlado, con riesgos a vigilar."
    elif score >= 55:
        estado = "ambar"
        lectura = "El centro puede operar, pero hay huecos que pueden falsear decisiones."
    else:
        estado = "rojo"
        lectura = "No conviene tomar decisiones fuertes sin reparar datos o riesgos operativos."

    if not riesgos:
        riesgos.append("Sin riesgos criticos detectados en la lectura rapida.")
    if not acciones:
        acciones.append("Mantener seguimiento y comparar objetivos contra resultado real.")
    causas.sort(key=lambda c: c.get("peso", 0), reverse=True)

    return {
        "score": score,
        "estado": estado,
        "lectura": lectura,
        "riesgos": riesgos[:6],
        "acciones": acciones[:6],
        "tendencia": tendencia,
        "causas": causas[:6],
    }


def _nombre_centro(conn, centro_id):
    if not centro_id:
        return "centro activo"
    tabla = "centros" if _exists(conn, "centros") else "centros_trabajo" if _exists(conn, "centros_trabajo") else ""
    if not tabla:
        return "centro activo"
    row = conn.execute(f"SELECT nombre FROM {tabla} WHERE id=?", (centro_id,)).fetchone()
    return _row_get(row, "nombre", "centro activo") if row else "centro activo"


def _nombre_empresa(conn, empresa_id):
    if not _exists(conn, "empresas"):
        return "empresa activa"
    row = conn.execute("SELECT nombre FROM empresas WHERE id=?", (empresa_id,)).fetchone()
    return _row_get(row, "nombre", "empresa activa") if row else "empresa activa"


def detectar_intencion(texto):
    base = _normalizar(texto)
    scores = []
    for clave, cfg in INTENCIONES.items():
        score = 0
        for palabra in cfg["palabras"]:
            if palabra in base:
                score += 2 if " " in palabra else 1
        if score:
            scores.append((score, clave))
    if not scores:
        return {
            "clave": "consulta_operativa",
            "modulo": "Operacion",
            "modo": "solo_lectura",
            "riesgo": "bajo",
        }
    scores.sort(reverse=True)
    clave = scores[0][1]
    cfg = INTENCIONES[clave]
    return {
        "clave": clave,
        "modulo": cfg["modulo"],
        "modo": cfg["modo"],
        "riesgo": cfg["riesgo"],
    }


def _intencion_desde_origen(origen):
    origen = _normalizar(origen)
    reglas = [
        (("produccion", "recetas", "menus"), "pedidos_produccion"),
        (("recepcion", "reservas", "habitaciones", "pms"), "pms_overbooking"),
        (("rrhh", "empleados", "nominas", "fichajes", "cuadrantes"), "nominas_borrador"),
        (("compras", "pedidos", "proveedores", "productos", "stock"), "pedidos_produccion"),
        (("mantenimiento", "ordenes", "activos"), "mantenimiento"),
        (("contabilidad", "facturas", "presupuesto", "costes"), "revisar_costes"),
        (("asistente/operativo", "asistente/gestion"), "revisar_costes"),
        (("experiencia", "quejas", "clientes"), "guest_service"),
        (("pisos", "housekeeping"), "mantenimiento"),
        (("appcc", "prl", "formacion"), "prl_appcc"),
    ]
    for tokens, clave in reglas:
        if any(token in origen for token in tokens):
            cfg = INTENCIONES.get(clave)
            if cfg:
                return {
                    "clave": clave,
                    "modulo": cfg["modulo"],
                    "modo": cfg["modo"],
                    "riesgo": cfg["riesgo"],
                    "origen_inferido": True,
                }
    return None


def detectar_intencion_contextual(texto, contexto):
    intencion = detectar_intencion(texto)
    if intencion["clave"] != "consulta_operativa":
        intencion["origen_inferido"] = False
        return intencion
    origen = (contexto or {}).get("origen_pantalla") or ""
    inferida = _intencion_desde_origen(origen)
    if inferida:
        return inferida
    intencion["origen_inferido"] = False
    return intencion


def _pasaporte_datos(contexto):
    ind = contexto.get("indicadores") or {}
    diag = contexto.get("diagnosticos") or {}
    sim = contexto.get("simulacion") or {}
    huecos = contexto.get("huecos") or []
    real_interno = []
    if ind:
        real_interno.append("indicadores operativos del centro")
    if diag:
        real_interno.append("diagnosticos por modulo")
    if contexto.get("kpis"):
        real_interno.append("KPIs del sistema nervioso")
    if contexto.get("ultimos"):
        real_interno.append("ultimos registros operativos")
    simulado = []
    if sim.get("resumen") or sim.get("escenarios") or sim.get("corridas") or sim.get("ultima_prueba_fuego"):
        simulado.append("resultados del simulador del centro activo")
    supuesto = []
    if sim.get("escenarios"):
        supuesto.append("parametros de escenarios cargados por direccion")
    externo = []
    if (diag.get("alergenos") or {}).get("propuestas_fuente_pendientes", 0) > 0:
        externo.append("propuestas de recetas o alergenos desde fuentes externas pendientes")
    if (diag.get("aprendizaje") or {}).get("memorias_activas", 0) > 0:
        real_interno.append("memoria operativa validada")

    penalizacion = min(45, len(huecos) * 4)
    huecos_texto = " | ".join(_normalizar(h) for h in huecos)
    huecos_criticos = (
        "sin ingredientes",
        "sin precio",
        "sin alergenos",
        "sin habitacion",
        "sin contrato",
        "presupuesto",
        "appcc no ok",
        "verifactu",
        "seguridad social",
        "prenomina",
        "proveedores activos",
        "menus planificados",
    )
    penalizacion += min(35, sum(8 for token in huecos_criticos if token in huecos_texto))
    if not ind:
        penalizacion += 15
    if not diag:
        penalizacion += 15
    if externo:
        penalizacion += 5
    confianza = max(10, min(100, 100 - penalizacion))
    if confianza >= 80:
        estado = "alta"
        lectura = "Hay base suficiente para recomendar con prudencia."
    elif confianza >= 55:
        estado = "media"
        lectura = "Se puede orientar, pero hay huecos que pueden cambiar la decision."
    else:
        estado = "baja"
        lectura = "Faltan datos fuertes; conviene completar informacion antes de decidir."

    return {
        "confianza": confianza,
        "estado": estado,
        "lectura": lectura,
        "real_interno": real_interno,
        "simulado": simulado,
        "supuesto": supuesto,
        "externo": externo,
        "huecos": huecos[:8],
        "regla": "no mezclar real, simulacion, supuestos y fuentes externas en una misma conclusion sin avisarlo",
    }


def _madurez_prebeta(contexto):
    ind = contexto.get("indicadores") or {}
    diag = contexto.get("diagnosticos") or {}
    brief = contexto.get("brief") or {}
    pasaporte = contexto.get("pasaporte_datos") or _pasaporte_datos(contexto)
    sim = contexto.get("simulacion") or {}
    huecos = contexto.get("huecos") or []
    checks = []
    roturas = []

    def check(nombre, ok, detalle, accion):
        estado = "ok" if ok else "pendiente"
        checks.append({"nombre": nombre, "estado": estado, "detalle": detalle, "accion": accion})
        if not ok:
            roturas.append({"nombre": nombre, "detalle": detalle, "accion": accion})

    check(
        "cerebro_contexto",
        bool(ind) and bool(diag) and bool(pasaporte),
        "El cerebro necesita indicadores, diagnosticos y pasaporte de datos.",
        "Completar contexto operativo antes de automatizar respuestas.",
    )
    check(
        "centro_activo",
        bool(contexto.get("empresa_id")) and bool(contexto.get("centro_id")),
        "Debe existir empresa y centro activo para no mezclar datos.",
        "Revisar selector de empresa/centro y filtros por centro.",
    )
    check(
        "datos_base",
        ind.get("empleados_activos", 0) > 0 and (ind.get("reservas_activas", 0) > 0 or (ind.get("presupuesto_comparable") and ind.get("presupuesto_ingresos", 0) > 0)),
        "El centro necesita empleados y al menos reservas o presupuesto para analizar gestion real.",
        "Completar reservas/presupuesto o marcar centro como laboratorio.",
    )
    prod = diag.get("produccion") or {}
    check(
        "cocina_compras",
        prod.get("recetas_sin_ingredientes", 0) == 0 and prod.get("productos_sin_precio", 0) == 0,
        "Recetas sin ingredientes o productos sin precio falsean escandallos, compras y margen.",
        "Priorizar recetas usadas en menu y productos de compra recurrente.",
    )
    pms = diag.get("pms") or {}
    check(
        "pms_recepcion",
        pms.get("reservas_sin_habitacion", 0) == 0 and pms.get("reservas_sin_precio", 0) == 0,
        "Reservas sin habitacion o precio rompen disponibilidad, revenue y margen.",
        "Asignar habitaciones y completar tarifas/canales antes de simular venta.",
    )
    check(
        "simulador",
        bool(sim.get("resumen") or sim.get("corridas") or sim.get("ultima_prueba_fuego")),
        "Debe existir prueba de simulador o laboratorio para ver donde rompe.",
        "Ejecutar control sano y prueba bruta del centro/laboratorio.",
    )
    check(
        "salud_operativa",
        int(brief.get("score") or 0) >= 70 and pasaporte.get("confianza", 0) >= 65,
        "La salud y confianza deben ser suficientes para enseñar sin confundir.",
        "Resolver causas principales del brief y huecos criticos.",
    )

    if checks[0]["estado"] != "ok":
        fase = "fase_cerebro"
        lectura = "Primero hay que terminar inteligencia, contexto y explicacion del cerebro."
    elif checks[5]["estado"] != "ok":
        fase = "fase_simulador"
        lectura = "El cerebro ya tiene base, pero falta probar con simulador antes de hablar de beta."
    elif roturas:
        fase = "fase_modulos"
        lectura = "El simulador o los diagnosticos detectan huecos; toca reparar modulo por modulo."
    elif len(huecos) > 0:
        fase = "fase_resimulacion"
        lectura = "No hay rotura fuerte, pero quedan huecos clasificados; toca repetir pruebas tras corregir."
    else:
        fase = "fase_beta_candidata"
        lectura = "El centro podria entrar en beta candidata: no perfecto, pero demostrable sin romper lo basico."

    return {
        "fase": fase,
        "lectura": lectura,
        "checks": checks,
        "pendientes": roturas[:8],
        "regla": "la beta llega al final: cerebro, simulador, modulos, resimulacion y solo despues beta candidata",
    }


def _siguiente_accion_prebeta(contexto):
    madurez = contexto.get("madurez_prebeta") or _madurez_prebeta(contexto)
    fase = madurez.get("fase") or "fase_cerebro"
    pendientes = madurez.get("pendientes") or []
    brief = contexto.get("brief") or {}
    causas = brief.get("causas") or []
    diag = contexto.get("diagnosticos") or {}
    sim = contexto.get("simulacion") or {}

    if fase == "fase_cerebro":
        return {
            "prioridad": "alta",
            "modulo": "Cerebro operativo",
            "accion": "Completar contexto, contrato operativo, pasaporte de datos y explicacion de huecos.",
            "motivo": "Sin cerebro claro, el simulador genera ruido y no aprendizaje.",
            "salida": "Respuesta trazable: datos reales, simulados, supuestos, huecos y siguiente paso.",
        }
    if fase == "fase_simulador":
        return {
            "prioridad": "alta",
            "modulo": "Simulador",
            "accion": "Ejecutar control sano y prueba bruta del centro o laboratorio antes de tocar beta.",
            "motivo": "El cerebro necesita pruebas para separar fallo de codigo, dato incompleto y riesgo operativo.",
            "salida": "Informe con roturas clasificadas: bloqueante, revision operativa, coste real o simulacion extrema.",
        }
    if fase == "fase_modulos":
        pendiente = pendientes[0] if pendientes else (causas[0] if causas else {})
        return {
            "prioridad": "alta",
            "modulo": pendiente.get("nombre") or pendiente.get("modulo") or "Modulo con huecos",
            "accion": pendiente.get("accion") or "Reparar el primer modulo que bloquea datos fiables.",
            "motivo": pendiente.get("detalle") or pendiente.get("motivo") or "Hay huecos o roturas que impiden confiar en la operacion.",
            "salida": "Modulo probado: abrir, listar, filtrar por centro, crear/editar si aplica y no romper.",
        }
    if fase == "fase_resimulacion":
        return {
            "prioridad": "media",
            "modulo": "Simulador",
            "accion": "Repetir simulacion despues de las correcciones y comparar contra la corrida anterior.",
            "motivo": "Una reparacion sin resimulacion no demuestra que el sistema aguante.",
            "salida": "Comparativa antes/despues con score, criticas, altas, medias y aprendizaje.",
        }

    # fase_beta_candidata
    riesgo_sim = (sim.get("resumen") or {}).get("criticas") or (sim.get("ultima_prueba_fuego") or {}).get("criticas")
    return {
        "prioridad": "media" if not riesgo_sim else "alta",
        "modulo": "Preparacion beta",
        "accion": "Preparar demo controlada solo con flujos que ya aguantan y lista honesta de pendientes.",
        "motivo": "La beta candidata no es producto cerrado; es una primera version mostrable y medible.",
        "salida": "Checklist beta: usuario, centro demo, datos, recorrido, limites, soporte y feedback.",
    }


def _lectura_recetas(contexto):
    diag = contexto.get("diagnosticos") or {}
    recetas = diag.get("recetas") or {}
    prod = diag.get("produccion") or {}
    muestras = contexto.get("recetas_muestras") or {}

    usadas_menu_sin_ing = int(recetas.get("usadas_menu_sin_ingredientes") or 0)
    usadas_prod_sin_ing = int(recetas.get("usadas_produccion_sin_ingredientes") or 0)
    usadas_menu_sin_alg = int(recetas.get("usadas_menu_sin_alergenos") or 0)
    usadas_prod_sin_alg = int(recetas.get("usadas_produccion_sin_alergenos") or 0)
    productos_sin_precio = int(recetas.get("productos_usados_sin_precio") or 0)
    propuestas_pendientes = int(recetas.get("propuestas_fuente_pendientes") or 0)
    catalogo_sin_ing = int(recetas.get("catalogo_sin_ingredientes") or prod.get("recetas_sin_ingredientes") or 0)
    catalogo_sin_alg = int(recetas.get("catalogo_sin_alergenos") or 0)
    bloqueos_ingredientes = usadas_menu_sin_ing + usadas_prod_sin_ing
    bloqueos_alergenos = usadas_menu_sin_alg + usadas_prod_sin_alg

    if not recetas.get("datos_basicos_disponibles") or int(recetas.get("catalogo_total") or 0) <= 0:
        estado = "datos_insuficientes"
        prioridad = "alta"
        accion = "Revisar el catalogo y los datos de ingredientes, alergenos y precios antes de tomar decisiones."
        lectura = "No hay recetas o faltan datos para evaluar ingredientes, alergenos y precios; no se puede confirmar su seguridad ni su coste."
    elif bloqueos_ingredientes > 0:
        estado = "bloqueo_operativo"
        prioridad = "alta"
        accion = "Completar ingredientes, cantidades y raciones de las recetas ya usadas en menu o produccion."
        lectura = "Hay recetas que ya afectan servicio y no son calculables; pedidos, escandallos y coste por plato pueden salir falsos."
    elif bloqueos_alergenos > 0:
        estado = "bloqueo_seguridad"
        prioridad = "alta"
        accion = "Validar alergenos de recetas usadas antes de publicar cartas, etiquetas o buffet."
        lectura = "Las recetas usadas tienen huecos de alergenos; esto afecta seguridad alimentaria y comunicacion al cliente."
    elif productos_sin_precio > 0:
        estado = "coste_incompleto"
        prioridad = "media"
        accion = "Completar precio o proveedor principal de productos usados por recetas reales."
        lectura = "Las recetas pueden estar montadas, pero el coste por plato no es fiable si los productos usados no tienen precio."
    elif propuestas_pendientes > 0:
        estado = "validar_fuentes"
        prioridad = "media"
        accion = "Revisar propuestas de fuentes externas y aprobar o descartar antes de incorporarlas como receta real."
        lectura = "Hay ayuda externa pendiente; sirve como propuesta, no como verdad operativa hasta que cocina la valide."
    elif catalogo_sin_ing > 0 or catalogo_sin_alg > 0:
        estado = "saneamiento_catalogo"
        prioridad = "baja"
        accion = "Limpiar catalogo por familias, empezando por recetas mas usadas y platos de temporada."
        lectura = "El catalogo tiene huecos, pero no veo que bloqueen ahora menu o produccion del centro."
    else:
        estado = "pendiente_validacion"
        prioridad = "normal"
        accion = "Validar las fichas, alergenos y costes con cocina antes de utilizarlos."
        lectura = "No se detectan huecos con las comprobaciones disponibles; esto no valida alergenos ni garantiza costes correctos."

    return {
        "estado": estado,
        "prioridad": prioridad,
        "lectura": lectura,
        "accion": accion,
        "catalogo_total": int(recetas.get("catalogo_total") or prod.get("recetas_total") or 0),
        "catalogo_sin_ingredientes": catalogo_sin_ing,
        "catalogo_sin_alergenos": catalogo_sin_alg,
        "usadas_menu_sin_ingredientes": usadas_menu_sin_ing,
        "usadas_produccion_sin_ingredientes": usadas_prod_sin_ing,
        "usadas_menu_sin_alergenos": usadas_menu_sin_alg,
        "usadas_produccion_sin_alergenos": usadas_prod_sin_alg,
        "productos_usados_sin_precio": productos_sin_precio,
        "propuestas_fuente_pendientes": propuestas_pendientes,
        "muestras": muestras,
        "regla": "primero recetas que afectan menu/produccion; despues catalogo; fuentes externas siempre como propuesta validable",
    }


def _lectura_compras(contexto):
    diag = contexto.get("diagnosticos") or {}
    compras = diag.get("compras") or {}
    muestras = contexto.get("compras_muestras") or {}
    proveedores = int(compras.get("proveedores_activos") or 0)
    productos = int(compras.get("productos_comprables") or 0)
    sin_precio = int(compras.get("productos_sin_precio") or 0)
    sin_proveedor = int(compras.get("productos_sin_proveedor") or 0)
    pedidos_abiertos = int(compras.get("pedidos_abiertos") or 0)
    pedidos_sin_lineas = int(compras.get("pedidos_sin_lineas") or 0)
    recepciones_pendientes = int(compras.get("recepciones_pendientes_doc") or 0)

    if not compras.get("datos_basicos_disponibles"):
        estado = "datos_insuficientes"
        prioridad = "alta"
        accion = "Revisar el acceso y los datos de productos, proveedores y pedidos del centro antes de decidir."
        lectura = "No se pueden comprobar todos los datos de compras para este centro; no se puede concluir si faltan proveedores, precios o pedidos."
    elif proveedores <= 0:
        estado = "sin_proveedores"
        prioridad = "alta"
        accion = "Crear proveedores base por familia antes de generar pedidos reales."
        lectura = "Compras no puede trabajar sin proveedores activos; cualquier pedido seria ficticio o incompleto."
    elif sin_proveedor > 0:
        estado = "catalogo_sin_proveedor"
        prioridad = "alta"
        accion = "Asignar proveedor principal o alternativo a productos comprables usados por cocina/economato."
        lectura = "Hay productos comprables sin proveedor; pedidos por proveedor y comparativas no seran fiables."
    elif sin_precio > 0:
        estado = "catalogo_sin_precio"
        prioridad = "media"
        accion = "Cargar precio estimado, ultimo precio o tarifa por proveedor para productos comprables."
        lectura = "Hay productos sin precio; se puede pedir, pero no calcular margen ni presupuesto real."
    elif pedidos_sin_lineas > 0:
        estado = "pedido_incompleto"
        prioridad = "media"
        accion = "Revisar pedidos abiertos sin lineas antes de enviar, autorizar o recepcionar."
        lectura = "Hay pedidos abiertos sin lineas; pueden confundir compras, recepcion y tesoreria."
    elif recepciones_pendientes > 0:
        estado = "recepcion_pendiente"
        prioridad = "media"
        accion = "Validar documentos/recepciones pendientes antes de actualizar coste real o stock definitivo."
        lectura = "Hay documentos de compra sin validar; stock y coste pueden no cuadrar aun."
    elif productos <= 0:
        estado = "sin_catalogo_compras"
        prioridad = "media"
        accion = "Marcar productos visibles en compras y separar compra directa de produccion/venta."
        lectura = "No veo catalogo comprable para este centro; compras no puede proponer necesidades."
    else:
        estado = "pendiente_validacion"
        prioridad = "normal"
        accion = "Revisar precios, proveedores, plazos y recepciones antes de preparar pedidos."
        lectura = "No se detectan huecos con las comprobaciones disponibles; hay que validar los datos antes de preparar o enviar pedidos."

    return {
        "estado": estado,
        "prioridad": prioridad,
        "lectura": lectura,
        "accion": accion,
        "proveedores_activos": proveedores,
        "productos_comprables": productos,
        "productos_sin_precio": sin_precio,
        "productos_sin_proveedor": sin_proveedor,
        "pedidos_abiertos": pedidos_abiertos,
        "pedidos_sin_lineas": pedidos_sin_lineas,
        "importe_pedidos_abiertos": float(compras.get("importe_pedidos_abiertos") or 0),
        "recepciones_pendientes_doc": recepciones_pendientes,
        "muestras": muestras,
        "regla": "compras prepara borradores; no confirma pedidos, recepciones, precios ni stock sin validacion",
    }


def presupuesto_operativo_seleccionado(conn, empresa_id, centro_id, presupuesto_id):
    """Lee exclusivamente un presupuesto elegido y sus lineas del mismo centro."""
    requeridas = {"id", "empresa_id", "centro_id", "anio", "nombre"}
    lineas = {"presupuesto_id", "empresa_id", "centro_id", "anio", "tipo", "importe", "fiabilidad"}
    if centro_id is None or not presupuesto_id or not requeridas.issubset(_columns(conn, "contabilidad_presupuestos")) or not lineas.issubset(_columns(conn, "contabilidad_presupuesto_lineas")):
        return None
    try:
        cabecera = conn.execute(
            "SELECT id, nombre, anio FROM contabilidad_presupuestos WHERE id=? AND empresa_id=? AND centro_id=?",
            (presupuesto_id, empresa_id, centro_id),
        ).fetchone()
        if not cabecera:
            return None
        filas = conn.execute(
            "SELECT tipo, importe, fiabilidad FROM contabilidad_presupuesto_lineas WHERE presupuesto_id=? AND empresa_id=? AND centro_id=? AND anio=?",
            (presupuesto_id, empresa_id, centro_id, cabecera["anio"]),
        ).fetchall()
        tipos = {row["tipo"] for row in filas}
        pendientes = sum(1 for row in filas if row["importe"] is None or str(row["fiabilidad"] or "").lower() in ("", "pendiente"))
        supuestos = sum(1 for row in filas if str(row["fiabilidad"] or "").lower() in ("supuesto", "estimado", "simulado"))
        ingresos = sum(float(row["importe"] or 0) for row in filas if row["tipo"] == "ingreso")
        gastos = sum(float(row["importe"] or 0) for row in filas if row["tipo"] == "gasto")
        return {"id": cabecera["id"], "nombre": cabecera["nombre"], "anio": cabecera["anio"],
                "ingresos": ingresos, "gastos": gastos, "resultado": ingresos - gastos,
                "lineas": len(filas), "pendientes": pendientes, "supuestos": supuestos,
                "completo": bool({"ingreso", "gasto"}.issubset(tipos) and not pendientes)}
    except (ValueError, TypeError, sqlite3.Error):
        return None


def construir_contexto_operativo(conn, empresa_id, centro_id=None, presupuesto_id=None):
    hoy = date.today()
    contexto = {
        "fecha": hoy.isoformat(),
        "empresa_id": empresa_id,
        "centro_id": centro_id,
        "empresa": _nombre_empresa(conn, empresa_id),
        "centro": _nombre_centro(conn, centro_id),
        "indicadores": {},
        "diagnosticos": {},
        "herramientas": [],
        "modularidad": {},
        "objetivos": [],
        "kpis": {},
        "mapa_nervioso": {},
        "simulacion": {},
        "copiloto": {},
        "brief": {},
        "tendencia": {},
        "ultimos": {},
        "huecos": [],
    }
    contexto["presupuesto_seleccionado"] = presupuesto_operativo_seleccionado(conn, empresa_id, centro_id, presupuesto_id)
    ind = contexto["indicadores"]
    contexto["diagnosticos"] = _diagnosticos_operativos(conn, empresa_id, centro_id)
    contexto["herramientas"] = _herramientas_disponibles(conn)
    contexto["modularidad"] = _modularidad_operativa(contexto["herramientas"])
    contexto["objetivos"] = _objetivos_operativos(conn, empresa_id, centro_id)
    contexto["tendencia"] = _tendencia_snapshots(conn, empresa_id, centro_id)
    ind["empleados_activos"] = _count_table(conn, "empleados", empresa_id, centro_id, "COALESCE(activo,1)=1")
    ind["fichajes_mes"] = _count_table(conn, "fichajes", empresa_id, centro_id, "substr(fecha_hora,1,7)=?", (hoy.strftime("%Y-%m"),))
    ind["nominas_pendientes"] = _count_table(conn, "nominas", empresa_id, centro_id, "COALESCE(estado,'pendiente') NOT IN ('pagada','pagado','cerrada','cerrado')") if _exists(conn, "nominas") else 0
    ind["reservas_activas"] = _count_table(conn, "reservas_recepcion", empresa_id, centro_id, "COALESCE(estado,'') NOT IN ('cancelada','cancelado','no_show','no show')")
    ind["partes_produccion_abiertos"] = _count_table(conn, "partes_produccion", empresa_id, centro_id, "COALESCE(estado,'borrador') NOT IN ('cerrado','cerrada','finalizado','finalizada','cancelado','cancelada')")
    ind["pedidos_abiertos"] = _count_table(conn, "pedidos", empresa_id, centro_id, "COALESCE(estado,'') NOT IN ('cerrado','cerrada','recibido','recibida','cancelado','cancelada')")
    ind["ordenes_mantenimiento_abiertas"] = _count_table(conn, "ordenes_trabajo", empresa_id, centro_id, "COALESCE(estado,'') NOT IN ('cerrado','cerrada','resuelto','resuelta','finalizado','finalizada','cancelado','cancelada')")
    ind["documentos_pendientes_ia"] = _count_table(conn, "asistente_documentos_lectura", empresa_id, centro_id, "COALESCE(estado,'pendiente_revision') NOT IN ('validado','descartado')")
    ind["recomendaciones_pendientes"] = _count_table(conn, "asistente_recomendaciones", empresa_id, None, "COALESCE(estado,'pendiente')='pendiente'")
    ind["presupuesto_ingresos"] = _sum_table(conn, "contabilidad_presupuesto_lineas", "importe", empresa_id, centro_id, "tipo='ingreso'")
    ind["presupuesto_gastos"] = _sum_table(conn, "contabilidad_presupuesto_lineas", "importe", empresa_id, centro_id, "tipo='gasto'")
    ind["presupuesto_resultado"] = ind["presupuesto_ingresos"] - ind["presupuesto_gastos"]
    # La suma anterior puede incluir diferentes periodos y escenarios.
    ind["presupuesto_comparable"] = False
    contexto["kpis"] = _kpis_operativos(contexto)
    contexto["kpis"]["items"] = _aplicar_tendencia_kpis(conn, empresa_id, centro_id, contexto["kpis"].get("items") or [])
    contexto["mapa_nervioso"] = _mapa_nervioso(contexto)
    contexto["simulacion"] = _simulador_contexto(conn, empresa_id, centro_id)
    diag = contexto["diagnosticos"]

    if ind["empleados_activos"] == 0:
        contexto["huecos"].append("No veo empleados activos en el centro o falta enlazar empleados con centro.")
    elif diag["rrhh"]["empleados_sin_contrato"] > 0:
        contexto["huecos"].append(f"Hay {diag['rrhh']['empleados_sin_contrato']} empleados activos sin contrato activo detectado.")
    if ind["reservas_activas"] == 0:
        contexto["huecos"].append("No veo reservas activas para este centro.")
    elif diag["pms"]["reservas_sin_habitacion"] > 0:
        contexto["huecos"].append(f"Hay {diag['pms']['reservas_sin_habitacion']} reservas activas sin habitacion asignada.")
    if not ind["presupuesto_comparable"]:
        contexto["huecos"].append("No hay un presupuesto y periodo seleccionados para calcular un margen fiable.")
    if ind["partes_produccion_abiertos"] == 0:
        contexto["huecos"].append("No hay partes de produccion abiertos o planificados.")
    if diag["produccion"]["recetas_sin_ingredientes"] > 0:
        contexto["huecos"].append(f"Hay {diag['produccion']['recetas_sin_ingredientes']} recetas sin ingredientes; los pedidos y costes pueden salir falsos.")
    if diag["produccion"]["productos_sin_precio"] > 0:
        contexto["huecos"].append(f"Hay {diag['produccion']['productos_sin_precio']} productos sin precio; escandallos y presupuesto quedan incompletos.")
    if diag["menus"]["menus_7d"] == 0:
        contexto["huecos"].append("No veo menus planificados para los proximos 7 dias; cocina, compras y alergenos quedan sin una base fiable.")
    if not diag["compras"]["datos_basicos_disponibles"]:
        contexto["huecos"].append("No puedo comprobar todos los datos de compras para este centro; no se debe asumir que faltan proveedores o productos.")
    elif diag["compras"]["proveedores_activos"] == 0:
        contexto["huecos"].append("No veo proveedores activos; compras no puede proponer pedidos reales por proveedor.")
    if diag["alergenos"]["recetas_sin_alergenos"] > 0:
        contexto["huecos"].append(f"Hay {diag['alergenos']['recetas_sin_alergenos']} recetas sin alergenos; cartas, buffet y seguridad alimentaria quedan en riesgo.")
    if diag["alergenos"]["propuestas_fuente_pendientes"] > 0:
        contexto["huecos"].append(f"Hay {diag['alergenos']['propuestas_fuente_pendientes']} propuestas de recetas desde fuentes externas pendientes de validar.")
    if diag["appcc_prl"]["appcc_no_ok_mes"] > 0:
        contexto["huecos"].append(f"Hay {diag['appcc_prl']['appcc_no_ok_mes']} registros APPCC no OK este mes; debe revisarse antes de cerrar el servicio.")
    if diag["appcc_prl"]["prl_formaciones_pendientes"] > 0:
        contexto["huecos"].append(f"Hay {diag['appcc_prl']['prl_formaciones_pendientes']} formaciones PRL o seguridad pendientes.")
    if diag["tpv_sala"]["cajas_abiertas"] > 0:
        contexto["huecos"].append(f"Hay {diag['tpv_sala']['cajas_abiertas']} cajas TPV abiertas; el cierre y la contabilidad pueden quedar descuadrados.")
    if diag["animacion"]["actividades_7d"] == 0 and ind["reservas_activas"] > 0:
        contexto["huecos"].append("Hay reservas activas pero no veo programa de animacion para los proximos 7 dias.")
    if diag["verifactu"]["estructura_preparada"] == 0 and diag["verifactu"]["facturas_venta_mes"] > 0:
        contexto["huecos"].append("Hay facturas de venta este mes, pero no veo estructura Verifactu preparada para huella, QR, registro y trazabilidad.")
    elif diag["verifactu"]["estructura_preparada"] == 0:
        contexto["huecos"].append("Verifactu no esta preparado todavia; conviene dejarlo en pruebas antes de usar facturacion real obligatoria.")
    elif diag["verifactu"]["pendientes_envio"] > 0:
        contexto["huecos"].append(f"Hay {diag['verifactu']['pendientes_envio']} registros Verifactu pendientes, con error o rechazados.")
    if diag["seguridad_social"]["estructura_preparada"] == 0:
        contexto["huecos"].append("No veo estructura de ficheros de Seguridad Social; RRHH no puede cerrar nominas con trazabilidad externa completa.")
    elif diag["seguridad_social"]["errores"] > 0:
        contexto["huecos"].append(f"Hay {diag['seguridad_social']['errores']} ficheros de Seguridad Social con error o rechazo.")
    elif diag["seguridad_social"]["pendientes"] > 0:
        contexto["huecos"].append(f"Hay {diag['seguridad_social']['pendientes']} ficheros de Seguridad Social pendientes de envio o respuesta.")
    if diag["seguridad_social"]["config_prenomina"] == 0:
        contexto["huecos"].append("No veo configuracion de prenomina; Seguridad Social, coste empresa y nominas pueden salir incompletos.")
    if diag["aprendizaje"]["recomendaciones_pendientes"] > 12:
        contexto["huecos"].append(f"Hay {diag['aprendizaje']['recomendaciones_pendientes']} recomendaciones pendientes; el cerebro acumula ruido si no se cierran.")
    if diag["aprendizaje"]["feedback_sin_resultado"] > 0:
        contexto["huecos"].append(f"Hay {diag['aprendizaje']['feedback_sin_resultado']} decisiones sin resultado explicado; falta aprendizaje util.")

    for table, key, fields in [
        ("asistente_recomendaciones", "recomendaciones", "id, modulo, prioridad, titulo, detalle, accion_propuesta, estado"),
        ("asistente_documentos_lectura", "documentos", "id, modulo_destino, tipo_documento, titulo, estado, propuesta"),
        ("asistente_operativo_senales", "senales", "id, modulo, origen, titulo, detalle, severidad, estado"),
        ("partes_produccion", "produccion", "id, fecha, turno, estado, observaciones"),
        ("ordenes_trabajo", "mantenimiento", "id, titulo, prioridad, estado, fecha_creacion"),
        ("menus_diarios", "menus", "id, fecha, nombre, estado, observaciones"),
        ("pedidos", "pedidos", "id, numero_pedido, proveedor_id, fecha_pedido, fecha_entrega, estado"),
        ("animacion_actividades", "animacion", "id, fecha, hora_inicio, titulo, tipo, estado"),
        ("appcc_registros_plan", "appcc", "id, fecha, resultado, observaciones, accion_correctiva"),
        ("tpv_caja_sesiones", "tpv_cajas", "id, fecha_apertura, estado, saldo_inicial, saldo_final, diferencia"),
        ("contabilidad_documentos", "contabilidad", "id, tipo, numero, fecha_documento, estado, total, pendiente"),
        ("verifactu_registros", "verifactu", "id, factura_id, numero_factura, fecha_expedicion, estado_envio, huella, fecha_creacion"),
        ("rrhh_seguridad_social_ficheros", "seguridad_social", "id, tipo, direccion, periodo, ccc, autorizado_red, canal, estado, fecha_generacion, fecha_envio, fecha_respuesta"),
        ("asistente_eventos_aprendizaje", "aprendizaje_eventos", "id, modulo, senal, decision, resultado, usuario, fecha_evento"),
        ("asistente_memoria", "memoria", "id, tipo, modulo, clave, valor, confianza, origen, estado, fecha_actualizacion"),
    ]:
        contexto["ultimos"][key] = [dict(row) for row in _latest_rows(conn, table, empresa_id, centro_id, fields=fields)]
    contexto["brief"] = _brief_operativo(contexto)
    contexto["copiloto"] = _lectura_copiloto(contexto)
    contexto["pasaporte_datos"] = _pasaporte_datos(contexto)
    contexto["madurez_prebeta"] = _madurez_prebeta(contexto)
    contexto["siguiente_accion_prebeta"] = _siguiente_accion_prebeta(contexto)
    contexto["compras_muestras"] = _sample_compras_prioritarias(conn, empresa_id, centro_id)
    contexto["compras_cerebro"] = _lectura_compras(contexto)
    contexto["recetas_muestras"] = _sample_recetas_prioritarias(conn, empresa_id, centro_id)
    contexto["recetas_cerebro"] = _lectura_recetas(contexto)
    return contexto


def _format_euros(value):
    try:
        number = float(value or 0)
    except Exception:
        number = 0.0
    text = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} EUR"


def _contrato_operativo(intencion, contexto):
    clave = (intencion or {}).get("clave") or "consulta_operativa"
    base = CONTRATOS_OPERATIVOS.get(clave) or {
        "respuesta_tipo": "consulta_operativa",
        "salida": "diagnostico y siguiente paso",
        "puede": (
            "leer datos del centro activo",
            "separar datos reales, supuestos y huecos",
            "preparar una propuesta medible",
        ),
        "no_debe": (
            "ejecutar cambios criticos sin confirmacion",
            "mezclar centros o empresas",
            "inventar datos que no existan",
        ),
        "datos": ("contexto_operativo", "indicadores", "diagnosticos"),
    }
    riesgo = (intencion or {}).get("riesgo") or "medio"
    modo = (intencion or {}).get("modo") or "solo_lectura"
    requiere_confirmacion = 1 if riesgo in ("alto", "medio") or modo != "solo_lectura" else 0
    salida_segura = base.get("salida") or "diagnostico operativo"
    if requiere_confirmacion:
        salida_segura = f"{salida_segura}; queda pendiente de validacion humana"
    return {
        "respuesta_tipo": base.get("respuesta_tipo", "consulta_operativa"),
        "salida_segura": salida_segura,
        "puede": list(base.get("puede") or []),
        "no_debe": list(base.get("no_debe") or []),
        "datos_necesarios": list(base.get("datos") or []),
        "requiere_confirmacion": requiere_confirmacion,
        "alcance": {
            "empresa": contexto.get("empresa"),
            "centro": contexto.get("centro"),
            "empresa_id": contexto.get("empresa_id"),
            "centro_id": contexto.get("centro_id"),
        },
        "regla": "leer, explicar y preparar; ejecutar solo con permisos, trazabilidad y confirmacion cuando sea critico",
    }


def _accion_base(intencion, texto, contexto):
    diagnosticos = contexto.get("diagnosticos") or {}
    herramientas = _herramientas_para_intencion(contexto, intencion["clave"])
    herramienta = herramientas[0] if herramientas else {}
    plan = _plan_operativo(intencion["clave"], diagnosticos)
    contrato = _contrato_operativo(intencion, contexto)
    accion = {
        "intencion": intencion["clave"],
        "modulo": intencion["modulo"],
        "accion": herramienta.get("clave") or "analizar",
        "descripcion": "Preparar analisis operativo",
        "payload": {
            "peticion": texto,
            "centro_id": contexto.get("centro_id"),
            "diagnostico": diagnosticos,
            "herramienta": herramienta,
            "plan": plan,
            "contrato": contrato,
        },
        "requiere_confirmacion": contrato["requiere_confirmacion"],
        "riesgo": intencion["riesgo"],
    }
    if intencion["clave"] == "nominas_borrador":
        accion.update({
            "accion": "preparar_nominas_borrador",
            "descripcion": "Preparar prenominas del periodo con fichajes, contratos, pluses, embargos y ausencias antes de pagar.",
        })
    elif intencion["clave"] == "crear_centro":
        accion.update({
            "accion": "preparar_alta_centro",
            "descripcion": "Crear borrador de empresa/centro con departamentos, puntos de venta, almacenes y permisos.",
        })
    elif intencion["clave"] == "pedidos_produccion":
        accion.update({
            "accion": "preparar_necesidades_compra",
            "descripcion": "Cruzar menus, produccion, stock y proveedores para proponer pedidos.",
        })
    elif intencion["clave"] == "factura_documento":
        accion.update({
            "accion": "leer_documento_y_preparar_asiento",
            "descripcion": "Leer documento, extraer datos y preparar propuesta contable o de compras.",
        })
    elif intencion["clave"] == "pms_overbooking":
        accion.update({
            "accion": "revisar_ocupacion_y_bloqueos",
            "descripcion": "Revisar reservas, bloqueos, habitaciones y riesgo de overbooking.",
        })
    elif intencion["clave"] == "mantenimiento":
        accion.update({
            "accion": "preparar_orden_mantenimiento",
            "descripcion": "Preparar orden de trabajo, prioridad, responsable y seguimiento.",
        })
    return accion


def _acciones_higiene_cerebro(intencion, contexto, recetas_cerebro, compras_cerebro):
    acciones = []
    clave = (intencion or {}).get("clave") or ""
    muestras_compras = (compras_cerebro or {}).get("muestras") or {}
    muestras_recetas = (recetas_cerebro or {}).get("muestras") or {}
    alcance = {
        "empresa_id": contexto.get("empresa_id"),
        "centro_id": contexto.get("centro_id"),
        "empresa": contexto.get("empresa"),
        "centro": contexto.get("centro"),
    }

    if (compras_cerebro or {}).get("productos_sin_proveedor", 0) > 0:
        acciones.append({
            "intencion": clave or "pedidos_produccion",
            "modulo": "Compras",
            "accion": "revisar_productos_sin_proveedor",
            "descripcion": (
                f"Asignar proveedor habitual a {compras_cerebro.get('productos_sin_proveedor', 0)} "
                "productos comprables antes de generar pedidos fiables."
            ),
            "payload": {
                "tipo": "higiene_catalogo_compras",
                "motivo": "productos_comprables_sin_proveedor",
                "alcance": alcance,
                "muestras": muestras_compras.get("sin_proveedor") or [],
                "siguiente_pantalla": "productos/proveedores",
                "regla": "no crear pedido automatico si el proveedor habitual no esta validado",
            },
            "requiere_confirmacion": 1,
            "riesgo": "medio",
        })
    if (compras_cerebro or {}).get("productos_sin_precio", 0) > 0:
        acciones.append({
            "intencion": clave or "revisar_costes",
            "modulo": "Compras",
            "accion": "revisar_productos_sin_precio",
            "descripcion": (
                f"Completar precio estimado o validado en {compras_cerebro.get('productos_sin_precio', 0)} "
                "productos comprables para que costes y escandallos no salgan falsos."
            ),
            "payload": {
                "tipo": "higiene_costes_compras",
                "motivo": "productos_comprables_sin_precio",
                "alcance": alcance,
                "muestras": muestras_compras.get("sin_precio") or [],
                "regla": "un precio estimado puede simular, pero no cerrar coste real",
            },
            "requiere_confirmacion": 1,
            "riesgo": "medio",
        })
    if (compras_cerebro or {}).get("pedidos_sin_lineas", 0) > 0:
        acciones.append({
            "intencion": clave or "pedidos_produccion",
            "modulo": "Compras",
            "accion": "revisar_pedidos_sin_lineas",
            "descripcion": (
                f"Revisar {compras_cerebro.get('pedidos_sin_lineas', 0)} pedidos abiertos sin lineas; "
                "no deben contar como compras reales."
            ),
            "payload": {
                "tipo": "higiene_pedidos",
                "motivo": "pedidos_abiertos_sin_lineas",
                "alcance": alcance,
                "muestras": muestras_compras.get("pedidos_sin_lineas") or [],
                "regla": "pedido sin lineas no alimenta compras, stock ni coste",
            },
            "requiere_confirmacion": 1,
            "riesgo": "medio",
        })

    recetas_sin_ingredientes = max(
        int((recetas_cerebro or {}).get("usadas_menu_sin_ingredientes") or 0),
        int((recetas_cerebro or {}).get("usadas_produccion_sin_ingredientes") or 0),
    )
    if recetas_sin_ingredientes > 0:
        acciones.append({
            "intencion": clave or "receta_coste",
            "modulo": "Recetas",
            "accion": "completar_recetas_usadas_sin_ingredientes",
            "descripcion": (
                f"Completar ingredientes de {recetas_sin_ingredientes} recetas usadas en menus o produccion "
                "antes de calcular compras, escandallos o costes por plato."
            ),
            "payload": {
                "tipo": "higiene_recetas",
                "motivo": "recetas_usadas_sin_ingredientes",
                "alcance": alcance,
                "muestras": muestras_recetas.get("sin_ingredientes") or [],
                "regla": "fuente externa propone; cocina valida antes de convertirlo en dato real",
            },
            "requiere_confirmacion": 1,
            "riesgo": "medio",
        })
    recetas_sin_alergenos = max(
        int((recetas_cerebro or {}).get("usadas_menu_sin_alergenos") or 0),
        int((recetas_cerebro or {}).get("usadas_produccion_sin_alergenos") or 0),
    )
    if recetas_sin_alergenos > 0:
        acciones.append({
            "intencion": clave or "receta_coste",
            "modulo": "Recetas/APPCC",
            "accion": "validar_alergenos_recetas_usadas",
            "descripcion": (
                f"Validar alergenos de {recetas_sin_alergenos} recetas usadas en menus o produccion "
                "antes de imprimir tarjetas o exponer buffet."
            ),
            "payload": {
                "tipo": "higiene_alergenos",
                "motivo": "recetas_usadas_sin_alergenos",
                "alcance": alcance,
                "muestras": muestras_recetas.get("sin_alergenos") or [],
                "regla": "alergeno dudoso se marca pendiente; no se imprime como seguro",
            },
            "requiere_confirmacion": 1,
            "riesgo": "alto",
        })
    if (recetas_cerebro or {}).get("productos_usados_sin_precio", 0) > 0:
        acciones.append({
            "intencion": clave or "revisar_costes",
            "modulo": "Recetas/Compras",
            "accion": "revisar_productos_usados_en_recetas_sin_precio",
            "descripcion": (
                f"Revisar {recetas_cerebro.get('productos_usados_sin_precio', 0)} productos usados en recetas sin precio; "
                "afectan escandallos y coste por plato."
            ),
            "payload": {
                "tipo": "higiene_escandallos",
                "motivo": "productos_usados_en_recetas_sin_precio",
                "alcance": alcance,
                "muestras": muestras_recetas.get("productos_sin_precio") or [],
                "regla": "coste por plato no es fiable si un ingrediente no tiene precio",
            },
            "requiere_confirmacion": 1,
            "riesgo": "medio",
        })
    return acciones[:4]


def responder_cerebro_operativo(texto, contexto):
    texto = (texto or "").strip()
    intencion = detectar_intencion_contextual(texto, contexto)
    ind = contexto.get("indicadores") or {}
    diag = contexto.get("diagnosticos") or {}
    contrato = _contrato_operativo(intencion, contexto)
    pasaporte = contexto.get("pasaporte_datos") or _pasaporte_datos(contexto)
    madurez = contexto.get("madurez_prebeta") or _madurez_prebeta(contexto)
    siguiente_prebeta = contexto.get("siguiente_accion_prebeta") or _siguiente_accion_prebeta(contexto)
    recetas_cerebro = contexto.get("recetas_cerebro") or _lectura_recetas(contexto)
    compras_cerebro = contexto.get("compras_cerebro") or _lectura_compras(contexto)
    acciones = []
    if intencion["modo"] != "solo_lectura":
        acciones.append(_accion_base(intencion, texto, contexto))
    for accion in _acciones_higiene_cerebro(intencion, contexto, recetas_cerebro, compras_cerebro):
        if not any(a.get("accion") == accion.get("accion") for a in acciones):
            acciones.append(accion)

    lineas = [
        f"Estoy mirando {contexto.get('empresa')} / {contexto.get('centro')} con enfoque de {intencion['modulo']}.",
        f"Intencion detectada: {intencion['clave'].replace('_', ' ')}. Modo: {intencion['modo'].replace('_', ' ')}. Riesgo: {intencion['riesgo']}.",
        f"Contrato operativo: respuesta {contrato['respuesta_tipo']}; salida segura: {contrato['salida_segura']}.",
        f"Brief operativo: {contexto.get('brief', {}).get('score', 0)}/100, estado {contexto.get('brief', {}).get('estado', 'sin_estado')}. {contexto.get('brief', {}).get('lectura', '')}",
        f"Madurez pre-beta: {madurez.get('fase')}. {madurez.get('lectura')}",
        f"Siguiente accion pre-beta: [{siguiente_prebeta.get('prioridad')}] {siguiente_prebeta.get('modulo')} - {siguiente_prebeta.get('accion')}",
        f"Cerebro recetas: {recetas_cerebro.get('estado')}. {recetas_cerebro.get('lectura')}",
        f"Cerebro compras: {compras_cerebro.get('estado')}. {compras_cerebro.get('lectura')}",
        f"Modularidad: {(contexto.get('modularidad') or {}).get('estado', 'sin_dato')} ({(contexto.get('modularidad') or {}).get('score', 0)}%). {(contexto.get('modularidad') or {}).get('lectura', '')}",
        f"Tendencia: {(contexto.get('tendencia') or {}).get('lectura', 'Sin tendencia historica suficiente')}",
        "",
        "Limites de seguridad:",
        f"- Puede: {'; '.join(contrato['puede'][:3])}.",
        f"- No debe: {'; '.join(contrato['no_debe'][:3])}.",
        f"- Datos base esperados: {', '.join(contrato['datos_necesarios'][:6])}.",
        "",
        f"Confianza de datos: {pasaporte.get('confianza', 0)}/100 ({pasaporte.get('estado', 'sin_dato')}). {pasaporte.get('lectura', '')}",
        f"- Real interno: {', '.join(pasaporte.get('real_interno') or ['sin datos claros'])}.",
        f"- Simulado/supuesto/externo: {', '.join((pasaporte.get('simulado') or []) + (pasaporte.get('supuesto') or []) + (pasaporte.get('externo') or [])) or 'nada relevante detectado'}.",
        "",
        "Datos que tengo ahora:",
        f"- Empleados activos: {ind.get('empleados_activos', 0)}. Fichajes del mes: {ind.get('fichajes_mes', 0)}. Nominas pendientes: {ind.get('nominas_pendientes', 0)}.",
        f"- Reservas activas: {ind.get('reservas_activas', 0)}. Partes de produccion abiertos: {ind.get('partes_produccion_abiertos', 0)}. Pedidos abiertos: {ind.get('pedidos_abiertos', 0)}.",
        f"- Ordenes mantenimiento abiertas: {ind.get('ordenes_mantenimiento_abiertas', 0)}. Documentos IA pendientes: {ind.get('documentos_pendientes_ia', 0)}.",
        ("- Presupuesto: seleccionado para consulta; margen pendiente de validar." if contexto.get("presupuesto_seleccionado") else
         "- Presupuesto: sin dato comparable; selecciona y revisa un presupuesto y periodo antes de calcular el margen."),
        f"- Tareas operativas: {(diag.get('tareas') or {}).get('abiertas', 0)} abiertas, {(diag.get('tareas') or {}).get('criticas', 0)} criticas, {(diag.get('tareas') or {}).get('bloqueadas', 0)} bloqueadas.",
    ]
    presupuesto = contexto.get("presupuesto_seleccionado")
    if presupuesto and presupuesto['lineas']:
        lineas.append(
            f"- Presupuesto seleccionado: {presupuesto['nombre']} ({presupuesto['anio']}): "
            f"ingresos {_format_euros(presupuesto['ingresos'])}, gastos {_format_euros(presupuesto['gastos'])}, "
            f"resultado previsto {_format_euros(presupuesto['resultado'])}; "
            f"{presupuesto['pendientes']} lineas pendientes y {presupuesto['supuestos']} estimadas, simuladas o supuestas. "
            f"{'Hay ingresos y gastos registrados sin lineas pendientes.' if presupuesto['completo'] else 'Faltan ingresos, gastos o hay lineas pendientes; el resultado es parcial.'} "
            "Es una prevision, no un resultado real ni una validacion de que incluya todos los costes."
        )
    elif presupuesto:
        lineas.append("- El presupuesto seleccionado no contiene lineas comprobables para este centro y ejercicio.")
    if intencion.get("origen_inferido"):
        lineas.insert(2, f"He inferido el modulo por la pantalla de origen: {contexto.get('origen_pantalla')}.")
    huecos = contexto.get("huecos") or []
    if huecos:
        lineas.extend(["", "Antes de decidir, veo estos huecos:"])
        lineas.extend(f"- {h}" for h in huecos[:5])

    causas = (contexto.get("brief") or {}).get("causas") or []
    if causas:
        lineas.extend(["", "Causas probables del semaforo:"])
        lineas.extend(f"- {c.get('modulo')}: {c.get('motivo')} (peso {c.get('peso')})." for c in causas[:4])

    objetivos = contexto.get("objetivos") or []
    if objetivos:
        lineas.extend(["", "Objetivos activos que debo respetar:"])
        for obj in objetivos[:4]:
            meta = f" ({obj.get('indicador')}: {obj.get('valor_objetivo')})" if obj.get("indicador") or obj.get("valor_objetivo") else ""
            lineas.append(f"- {obj.get('modulo')}: {obj.get('objetivo')}{meta}.")

    herramientas = _herramientas_para_intencion(contexto, intencion["clave"])
    if herramientas:
        lineas.extend(["", "Herramienta operativa relacionada:"])
        for h in herramientas[:2]:
            faltan = ", ".join(h.get("tablas_faltantes") or []) or "ninguna tabla base"
            lineas.append(
                f"- {h['descripcion']} Estado: {h['estado_real']} ({h['disponible_pct']}%). Faltan: {faltan}."
            )

    plan = _plan_operativo(intencion["clave"], diag)
    preguntas = _preguntas_siguientes(intencion["clave"], diag, contexto)
    lineas.extend(["", "Plan operativo propuesto:"])
    lineas.extend(f"{idx}. {paso}" for idx, paso in enumerate(plan, 1))
    lineas.extend(["", "Preguntas para afinar sin inventar:"])
    lineas.extend(f"- {p}" for p in preguntas)

    if intencion["clave"] == "nominas_borrador":
        rrhh = diag.get("rrhh") or {}
        lineas.extend([
            "",
            "Diagnostico nominas:",
            f"- Empleados activos: {rrhh.get('empleados_activos', 0)}. Sin contrato activo: {rrhh.get('empleados_sin_contrato', 0)}.",
            f"- Fichajes del mes: {rrhh.get('fichajes_mes', 0)}. Nominas del mes ya creadas: {rrhh.get('nominas_mes', 0)}. Pendientes: {rrhh.get('nominas_pendientes', 0)}.",
            "Para nominas no debo pagar ni cerrar nada de golpe. Primero preparo un borrador cruzando contrato, fichajes, ausencias, embargos, pluses y anticipos.",
            "Si falta algun dato, la accion queda bloqueada como borrador y direccion revisa antes de pagar.",
        ])
    elif intencion["clave"] == "revisar_costes":
        fin = diag.get("finanzas") or {}
        prod = diag.get("produccion") or {}
        if contexto.get("presupuesto_seleccionado"):
            lectura = "el resultado seleccionado es una prevision; hay que comprobar sus costes y supuestos antes de concluir que el centro es rentable"
        else:
            lectura = "no hay importes suficientes y comparables para determinar el margen; selecciona un presupuesto, periodo y revisa sus costes"
        lineas.extend([
            "",
            f"Lectura rapida: {lectura}.",
            f"- Recurrentes configurados: {fin.get('recurrentes', 0)}. Productos sin precio: {prod.get('productos_sin_precio', 0)}. Recetas sin ingredientes: {prod.get('recetas_sin_ingredientes', 0)}.",
            "La siguiente capa debe separar coste fijo, coste variable, coste laboral, coste por servicio y coste por plato para explicar el por que, no solo mostrar un numero.",
        ])
    elif intencion["clave"] == "pedidos_produccion":
        prod = diag.get("produccion") or {}
        muestras_compras = compras_cerebro.get("muestras") or {}
        lineas.extend([
            "",
            "Diagnostico produccion/compras:",
            f"- Partes abiertos: {prod.get('partes_abiertos', 0)}. Partes del mes: {prod.get('partes_mes', 0)}. Pedidos abiertos: {prod.get('pedidos_abiertos', 0)}.",
            f"- Recetas sin ingredientes: {prod.get('recetas_sin_ingredientes', 0)}. Recetas con cantidades mal: {prod.get('recetas_con_cantidad_mal', 0)}. Productos sin precio: {prod.get('productos_sin_precio', 0)}.",
            f"- Proveedores activos: {compras_cerebro.get('proveedores_activos', 0)}. Productos comprables: {compras_cerebro.get('productos_comprables', 0)}. Sin proveedor: {compras_cerebro.get('productos_sin_proveedor', 0)}.",
            f"- Pedidos abiertos: {compras_cerebro.get('pedidos_abiertos', 0)}. Pedidos sin lineas: {compras_cerebro.get('pedidos_sin_lineas', 0)}. Importe abierto: {_format_euros(compras_cerebro.get('importe_pedidos_abiertos'))}.",
            f"Siguiente accion compras: [{compras_cerebro.get('prioridad')}] {compras_cerebro.get('accion')}",
            "Para pedidos buenos necesito menus planificados, recetas con ingredientes reales, stock, proveedores y plazos.",
            "La propuesta correcta es calcular necesidades, descontar stock util, revisar APPCC y generar pedidos en borrador por proveedor.",
        ])
        if muestras_compras.get("sin_proveedor"):
            lineas.extend(["", "Productos comprables sin proveedor prioritarios:"])
            lineas.extend(f"- {p.get('nombre')} ({p.get('familia') or 'sin familia'})." for p in muestras_compras["sin_proveedor"][:6])
        if muestras_compras.get("sin_precio"):
            lineas.extend(["", "Productos comprables sin precio prioritarios:"])
            lineas.extend(f"- {p.get('nombre')} ({p.get('familia') or 'sin familia'})." for p in muestras_compras["sin_precio"][:6])
        if muestras_compras.get("pedidos_sin_lineas"):
            lineas.extend(["", "Pedidos abiertos sin lineas:"])
            lineas.extend(f"- Pedido {p.get('numero_pedido') or p.get('id')} estado {p.get('estado') or 'sin estado'}." for p in muestras_compras["pedidos_sin_lineas"][:6])
    elif intencion["clave"] == "receta_coste":
        muestras_recetas = recetas_cerebro.get("muestras") or {}
        muestra_ing = muestras_recetas.get("sin_ingredientes") or []
        muestra_alg = muestras_recetas.get("sin_alergenos") or []
        muestra_precio = muestras_recetas.get("productos_sin_precio") or []
        lineas.extend([
            "",
            "Diagnostico recetas:",
            f"- Catalogo total: {recetas_cerebro.get('catalogo_total', 0)}. Sin ingredientes en catalogo: {recetas_cerebro.get('catalogo_sin_ingredientes', 0)}. Sin alergenos en catalogo: {recetas_cerebro.get('catalogo_sin_alergenos', 0)}.",
            f"- Usadas en menu sin ingredientes: {recetas_cerebro.get('usadas_menu_sin_ingredientes', 0)}. Usadas en produccion sin ingredientes: {recetas_cerebro.get('usadas_produccion_sin_ingredientes', 0)}.",
            f"- Usadas en menu sin alergenos: {recetas_cerebro.get('usadas_menu_sin_alergenos', 0)}. Usadas en produccion sin alergenos: {recetas_cerebro.get('usadas_produccion_sin_alergenos', 0)}.",
            f"- Productos usados por recetas sin precio: {recetas_cerebro.get('productos_usados_sin_precio', 0)}. Propuestas externas pendientes: {recetas_cerebro.get('propuestas_fuente_pendientes', 0)}.",
            f"Siguiente accion recetas: [{recetas_cerebro.get('prioridad')}] {recetas_cerebro.get('accion')}",
            "Regla: una fuente externa o libro puede proponer ingredientes y alergenos, pero cocina debe validarlo antes de convertirlo en verdad de la ERP.",
        ])
        if muestra_ing:
            lineas.extend(["", "Primeras recetas a completar con ingredientes:"])
            lineas.extend(
                f"- {r.get('nombre')} (menu {r.get('usos_menu', 0)}, produccion {r.get('usos_produccion', 0)})."
                for r in muestra_ing[:6]
            )
        if muestra_alg:
            lineas.extend(["", "Primeras recetas a validar con alergenos:"])
            lineas.extend(
                f"- {r.get('nombre')} (menu {r.get('usos_menu', 0)}, produccion {r.get('usos_produccion', 0)})."
                for r in muestra_alg[:6]
            )
        if muestra_precio:
            lineas.extend(["", "Productos usados en recetas sin precio:"])
            lineas.extend(
                f"- {p.get('nombre')} ({p.get('recetas_afectadas', 0)} recetas afectadas)."
                for p in muestra_precio[:6]
            )
    elif intencion["clave"] == "factura_documento":
        lineas.extend([
            "",
            "Si subes una factura, albaran o foto, debe leerse como dato pendiente: extraer proveedor, fecha, importes, impuestos, centro, almacen y posible pedido relacionado.",
            "Hasta que alguien valide, no debe tocar contabilidad oficial ni stock definitivo.",
        ])
    elif intencion["clave"] == "consulta_operativa":
        lineas.extend([
            "",
            "Puedo ayudarte como capa operativa: dime si quieres revisar costes, reservas, nominas, produccion, mantenimiento, documentos, cocina, PRL o una inversion.",
            "La respuesta debe acabar siempre en una propuesta medible o en una lista clara de datos que faltan.",
        ])
    elif intencion["clave"] == "pms_overbooking":
        pms = diag.get("pms") or {}
        lineas.extend([
            "",
            "Diagnostico PMS:",
            f"- Reservas activas: {pms.get('reservas_activas', 0)}. Sin habitacion: {pms.get('reservas_sin_habitacion', 0)}. Sin precio: {pms.get('reservas_sin_precio', 0)}.",
            "La ERP debe prevenir el overbooking bloqueando cupos por canal, habitacion y fecha; si ocurre, debe proponer desvio interno o hotel de apoyo antes de improvisar en recepcion.",
        ])
    else:
        lineas.extend([
            "",
            "Propuesta: revisar el modulo indicado con datos internos, marcar riesgos y preparar el siguiente paso sin ejecutar cambios criticos sin confirmacion.",
        ])

    if acciones:
        lineas.extend(["", "Accion preparada en borrador:"])
        lineas.extend(f"- {a['descripcion']}" for a in acciones)
        if any((a.get("payload") or {}).get("tipo", "").startswith("higiene_") for a in acciones):
            lineas.append("Estas acciones son higiene de datos: preparan revision humana y no modifican productos, recetas, pedidos ni alergenos por si solas.")
    else:
        lineas.extend(["", "No he preparado ejecucion automatica: esta consulta queda en modo lectura/analisis."])

    return {
        "intencion": intencion["clave"],
        "modulo": intencion["modulo"],
        "modo": intencion["modo"],
        "riesgo": intencion["riesgo"],
        "respuesta": "\n".join(lineas),
        "acciones": acciones,
        "contexto_resumen": {
            "empresa": contexto.get("empresa"),
            "centro": contexto.get("centro"),
            "indicadores": ind,
            "diagnosticos": diag,
            "herramientas": herramientas,
            "plan": plan,
            "preguntas": preguntas,
            "objetivos": objetivos,
            "brief": contexto.get("brief") or {},
            "tendencia": contexto.get("tendencia") or {},
            "huecos": huecos,
            "contrato": contrato,
            "pasaporte_datos": pasaporte,
            "madurez_prebeta": madurez,
            "siguiente_accion_prebeta": siguiente_prebeta,
            "recetas_cerebro": recetas_cerebro,
            "compras_cerebro": compras_cerebro,
        },
    }


def sugerencia_contextual(origen_pantalla, contexto):
    contexto = dict(contexto or {})
    contexto["origen_pantalla"] = origen_pantalla or ""
    intencion = detectar_intencion_contextual("revisa esta pantalla", contexto)
    diag = contexto.get("diagnosticos") or {}
    herramientas = _herramientas_para_intencion(contexto, intencion["clave"])
    return {
        "origen_pantalla": origen_pantalla,
        "intencion": intencion["clave"],
        "modulo": intencion["modulo"],
        "modo": intencion["modo"],
        "riesgo": intencion["riesgo"],
        "origen_inferido": bool(intencion.get("origen_inferido")),
        "herramientas": herramientas[:2],
        "plan": _plan_operativo(intencion["clave"], diag),
        "preguntas": _preguntas_siguientes(intencion["clave"], diag, contexto),
    }

from helpers.appcc_plan_helper import ensure_appcc_plan_schema
from helpers.centros_helper import add_columna_si_falta
from helpers.operativa_helper import ensure_equipos_unificados_schema


APPCC_ISO_REFERENCIAS = [
    "Reglamento (CE) 852/2004: higiene alimentaria y procedimientos basados en APPCC.",
    "AESAN: guias de practicas correctas de higiene y aplicacion flexible de APPCC.",
    "ISO 22000: sistema de gestion de seguridad alimentaria, prerrequisitos, trazabilidad y mejora.",
]


def _bool(form, name):
    return 1 if form.get(name) in {"1", "on", "true", "si", "yes"} else 0


def _int(form, name, default=0):
    try:
        return int(form.get(name) or default)
    except Exception:
        return default


def _slug(text, default="CENTRO"):
    value = "".join(ch for ch in (text or "").upper() if ch.isalnum())
    return (value or default)[:12]


def ensure_centro_ia_schema(conn):
    add_columna_si_falta(conn, "recepcion_tipos_habitacion", "centro_id INTEGER")
    add_columna_si_falta(conn, "plan_limpieza", "centro_id INTEGER")
    ensure_appcc_plan_schema(conn)
    add_columna_si_falta(conn, "appcc_controles_plan", "centro_id INTEGER")
    ensure_equipos_unificados_schema(conn)


def construir_plan_centro_ia(form):
    nombre = (form.get("nombre") or "Nuevo centro").strip()
    tipo = form.get("tipo") or "hotel"
    habitaciones = _int(form, "habitaciones", 0)
    plantas = max(1, _int(form, "plantas", 1))
    tiene_cocina = _bool(form, "cocina_central")
    tiene_buffet = _bool(form, "buffet")
    restaurantes = max(0, _int(form, "restaurantes", 0))
    bares = max(0, _int(form, "bares", 0))
    piscina = _bool(form, "piscina")
    spa = _bool(form, "spa")

    departamentos = [
        {"nombre": "RECEPCION", "tipo": "recepcion", "descripcion": "Front desk, reservas, caja y atencion al cliente", "caps": {}},
        {"nombre": "PISOS", "tipo": "pisos", "descripcion": "Habitaciones, limpieza, lenceria y amenities", "caps": {}},
        {"nombre": "MANTENIMIENTO", "tipo": "mantenimiento", "descripcion": "Averias, preventivos, activos y partes tecnicos", "caps": {"gestiona_mantenimiento": 1, "permite_pedidos_internos": 1, "permite_pedidos_externos": 1}},
        {"nombre": "COMPRAS / ALMACEN", "tipo": "almacen", "descripcion": "Economato, compras, recepciones y stock central", "caps": {"gestiona_stock": 1, "permite_pedidos_externos": 1}},
        {"nombre": "ADMINISTRACION", "tipo": "general", "descripcion": "Direccion, contabilidad y administracion", "caps": {"permite_pedidos_externos": 1}},
        {"nombre": "RRHH", "tipo": "general", "descripcion": "Personal, cuadrantes, documentacion laboral y permisos", "caps": {}},
    ]
    if tiene_cocina:
        departamentos.append({
            "nombre": "COCINA CENTRAL",
            "tipo": "cocina",
            "descripcion": "Produccion, elaboraciones, APPCC, camaras y trazabilidad",
            "caps": {"es_cocina": 1, "independiente_cocina": 1, "gestiona_appcc": 1, "gestiona_stock": 1, "permite_pedidos_internos": 1, "permite_pedidos_externos": 1},
        })
    if restaurantes or tiene_buffet:
        departamentos.append({
            "nombre": "BARES/RESTAURANTES",
            "tipo": "restaurante",
            "descripcion": "Salas, barras, buffet y puntos de venta",
            "caps": {"gestiona_appcc": 1, "gestiona_stock": 1, "permite_tpv": 1, "permite_pedidos_internos": 1},
        })
    elif bares:
        departamentos.append({
            "nombre": "BARES",
            "tipo": "bar",
            "descripcion": "Barras, bebidas y puntos de venta",
            "caps": {"gestiona_appcc": 1, "gestiona_stock": 1, "permite_tpv": 1, "permite_pedidos_internos": 1},
        })

    zonas = ["Recepcion", "Lobby", "Pasillos", "Aseos comunes"]
    if piscina:
        zonas.extend(["Piscina", "Terraza piscina"])
    if spa:
        zonas.append("Spa")
    if tiene_cocina:
        zonas.extend(["Cocina", "Economato", "Camaras frigorificas", "Residuos cocina"])
    if restaurantes or bares or tiene_buffet:
        zonas.extend(["Sala restaurante", "Barra", "Office"])

    almacenes = []
    if tiene_cocina:
        almacenes.extend([
            {"nombre": "Economato central", "tipo": "seco", "departamento": "COCINA CENTRAL"},
            {"nombre": "Camara refrigerada cocina", "tipo": "refrigerado", "departamento": "COCINA CENTRAL", "rango_min": 0, "rango_max": 5},
            {"nombre": "Congelador cocina", "tipo": "congelado", "departamento": "COCINA CENTRAL", "rango_min": -24, "rango_max": -18},
        ])
    almacenes.append({"nombre": "Almacen mantenimiento", "tipo": "seco", "departamento": "MANTENIMIENTO"})
    almacenes.append({"nombre": "Almacen pisos", "tipo": "seco", "departamento": "PISOS"})
    if restaurantes or bares or tiene_buffet:
        almacenes.append({"nombre": "Almacen bebidas", "tipo": "seco", "departamento": "BARES/RESTAURANTES" if restaurantes or tiene_buffet else "BARES"})

    puntos_venta = []
    for i in range(bares):
        puntos_venta.append({"nombre": f"Bar {i + 1}", "tipo": "bar"})
    for i in range(restaurantes):
        puntos_venta.append({"nombre": f"Restaurante {i + 1}", "tipo": "restaurante"})
    if tiene_buffet:
        puntos_venta.append({"nombre": "Buffet", "tipo": "buffet"})

    controles = ["Recepcion mercancias", "Temperaturas de frio", "Limpieza y desinfeccion", "Trazabilidad de lotes", "Gestion de incidencias"]
    if tiene_cocina:
        controles.extend(["Descongelacion carnes y pescados", "Coccion/elaboracion", "Abatimiento/enfriamiento", "Regeneracion", "Mermas y bloqueos"])

    return {
        "nombre": nombre,
        "codigo": _slug(nombre),
        "tipo": tipo,
        "habitaciones": habitaciones,
        "plantas": plantas,
        "departamentos": departamentos,
        "zonas": zonas,
        "almacenes": almacenes,
        "puntos_venta": puntos_venta,
        "controles": controles,
        "referencias": APPCC_ISO_REFERENCIAS,
    }


def _get_or_create(conn, select_sql, select_params, insert_sql, insert_params):
    row = conn.execute(select_sql, select_params).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(insert_sql, insert_params)
    return cur.lastrowid


def aplicar_plan_centro_ia(conn, empresa_id, plan):
    ensure_centro_ia_schema(conn)
    centro_id = _get_or_create(
        conn,
        "SELECT id FROM centros_trabajo WHERE empresa_id = ? AND LOWER(nombre) = LOWER(?) LIMIT 1",
        (empresa_id, plan["nombre"]),
        "INSERT INTO centros_trabajo (nombre, codigo, descripcion, empresa_id, activo) VALUES (?, ?, ?, ?, 1)",
        (plan["nombre"], plan["codigo"], f"Centro generado por asistente IA: {plan['tipo']}", empresa_id),
    )

    bloque_codigo = f"GEN{centro_id}"
    bloque_id = _get_or_create(
        conn,
        "SELECT id FROM establecimiento_bloques WHERE empresa_id = ? AND centro_id = ? AND codigo = ? LIMIT 1",
        (empresa_id, centro_id, bloque_codigo),
        "INSERT INTO establecimiento_bloques (codigo, nombre, descripcion, centro_id, activo, empresa_id) VALUES (?, 'General', 'Bloque principal', ?, 1, ?)",
        (bloque_codigo, centro_id, empresa_id),
    )
    planta_ids = []
    for numero in range(plan["plantas"]):
        nombre = "Planta baja" if numero == 0 else f"Planta {numero}"
        planta_ids.append(_get_or_create(
            conn,
            "SELECT id FROM establecimiento_plantas WHERE empresa_id = ? AND centro_id = ? AND bloque_id = ? AND numero = ? LIMIT 1",
            (empresa_id, centro_id, bloque_id, str(numero)),
            "INSERT INTO establecimiento_plantas (bloque_id, numero, nombre, descripcion, activo, empresa_id, centro_id) VALUES (?, ?, ?, '', 1, ?, ?)",
            (bloque_id, str(numero), nombre, empresa_id, centro_id),
        ))

    dep_ids = {}
    for dep in plan["departamentos"]:
        caps = dep["caps"]
        dep_id = _get_or_create(
            conn,
            "SELECT id FROM departamentos WHERE empresa_id = ? AND centro_id = ? AND LOWER(nombre) = LOWER(?) LIMIT 1",
            (empresa_id, centro_id, dep["nombre"]),
            """
            INSERT INTO departamentos
            (nombre, descripcion, activo, empresa_id, centro_id, tipo_operativo, es_cocina, independiente_cocina,
             gestiona_appcc, gestiona_stock, gestiona_mantenimiento, permite_tpv, permite_pedidos_internos, permite_pedidos_externos)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dep["nombre"], dep["descripcion"], empresa_id, centro_id, dep["tipo"],
                caps.get("es_cocina", 0), caps.get("independiente_cocina", 0), caps.get("gestiona_appcc", 0),
                caps.get("gestiona_stock", 0), caps.get("gestiona_mantenimiento", 0), caps.get("permite_tpv", 0),
                caps.get("permite_pedidos_internos", 0), caps.get("permite_pedidos_externos", 0),
            ),
        )
        dep_ids[dep["nombre"]] = dep_id

    sub_base = {
        "RECEPCION": ["Reservas", "Check-in / Check-out"],
        "PISOS": ["Habitaciones", "Zonas comunes", "Lenceria"],
        "MANTENIMIENTO": ["Correctivo", "Preventivo"],
        "COMPRAS / ALMACEN": ["Economato", "Recepcion mercancias"],
        "COCINA CENTRAL": ["Cocina caliente", "Cuarto frio", "Pasteleria", "Carnes y pescados"],
        "BARES/RESTAURANTES": ["Sala", "Barra", "Buffet"],
        "BARES": ["Barra"],
    }
    for dep_nombre, nombres in sub_base.items():
        dep_id = dep_ids.get(dep_nombre)
        if not dep_id:
            continue
        for nombre in nombres:
            _get_or_create(
                conn,
                "SELECT id FROM subdepartamentos WHERE empresa_id = ? AND departamento_id = ? AND LOWER(nombre) = LOWER(?) LIMIT 1",
                (empresa_id, dep_id, nombre),
                "INSERT INTO subdepartamentos (departamento_id, nombre, descripcion, activo, empresa_id, centro_id) VALUES (?, ?, '', 1, ?, ?)",
                (dep_id, nombre, empresa_id, centro_id),
            )

    for zona in plan["zonas"]:
        _get_or_create(
            conn,
            "SELECT id FROM establecimiento_zonas WHERE empresa_id = ? AND centro_id = ? AND LOWER(nombre) = LOWER(?) LIMIT 1",
            (empresa_id, centro_id, zona),
            """
            INSERT INTO establecimiento_zonas
            (nombre, tipo, bloque_id, planta_id, bloque, planta, frecuencia_limpieza, appcc_critico, activo, empresa_id, centro_id)
            VALUES (?, 'zona_comun', ?, ?, 'General', 'Planta baja', 'diaria', ?, 1, ?, ?)
            """,
            (zona, bloque_id, planta_ids[0] if planta_ids else None, 1 if zona.lower() in {"cocina", "economato", "camaras frigorificas", "residuos cocina"} else 0, empresa_id, centro_id),
        )

    if plan["habitaciones"]:
        tipo_id = _get_or_create(
            conn,
            "SELECT id FROM recepcion_tipos_habitacion WHERE empresa_id = ? AND centro_id = ? AND codigo = 'STD' LIMIT 1",
            (empresa_id, centro_id),
            """
            INSERT INTO recepcion_tipos_habitacion
            (codigo, nombre, capacidad_base, capacidad_max, descripcion, activo, empresa_id, centro_id)
            VALUES ('STD', 'Standard', 2, 2, 'Tipo base generado por asistente', 1, ?, ?)
            """,
            (empresa_id, centro_id),
        )
        for n in range(1, plan["habitaciones"] + 1):
            planta_num = (n - 1) % max(1, plan["plantas"])
            numero = f"{planta_num + 1}{n:02d}"
            _get_or_create(
                conn,
                "SELECT id FROM habitaciones_recepcion WHERE empresa_id = ? AND centro_id = ? AND numero = ? LIMIT 1",
                (empresa_id, centro_id, numero),
                """
                INSERT INTO habitaciones_recepcion
                (numero, nombre, tipo, planta, capacidad, estado, centro_id, observaciones, empresa_id, activo, tipo_habitacion_id, bloque, bloque_id, planta_id)
                VALUES (?, ?, 'Standard', ?, 2, 'disponible', ?, '', ?, 1, ?, 'General', ?, ?)
                """,
                (numero, f"Habitacion {numero}", str(planta_num), centro_id, empresa_id, tipo_id, bloque_id, planta_ids[planta_num] if planta_ids else None),
            )

    almacen_ids = {}
    for alm in plan["almacenes"]:
        dep_id = dep_ids.get(alm["departamento"])
        if not dep_id:
            continue
        almacen_ids[alm["nombre"]] = _get_or_create(
            conn,
            "SELECT id FROM almacenes WHERE empresa_id = ? AND centro_id = ? AND LOWER(nombre) = LOWER(?) LIMIT 1",
            (empresa_id, centro_id, alm["nombre"]),
            """
            INSERT INTO almacenes
            (nombre, tipo, ubicacion, responsable, activo, empresa_id, departamento_id, rango_min, rango_max, centro_id)
            VALUES (?, ?, ?, '', 1, ?, ?, ?, ?, ?)
            """,
            (alm["nombre"], alm["tipo"], alm["nombre"], empresa_id, dep_id, alm.get("rango_min"), alm.get("rango_max"), centro_id),
        )

    pv_dep = dep_ids.get("BARES/RESTAURANTES") or dep_ids.get("BARES")
    almacen_bebidas = almacen_ids.get("Almacen bebidas")
    for pv in plan["puntos_venta"]:
        _get_or_create(
            conn,
            "SELECT id FROM puntos_venta WHERE empresa_id = ? AND centro_id = ? AND LOWER(nombre) = LOWER(?) LIMIT 1",
            (empresa_id, centro_id, pv["nombre"]),
            """
            INSERT INTO puntos_venta
            (nombre, tipo, almacen_id, activo, departamento_id, imprimir_comanda, empresa_id, tpv_tipo, tpv_control_buffet, centro_id)
            VALUES (?, ?, ?, 1, ?, 1, ?, ?, ?, ?)
            """,
            (pv["nombre"], pv["tipo"], almacen_bebidas, pv_dep, empresa_id, pv["tipo"], 1 if pv["tipo"] == "buffet" else 0, centro_id),
        )

    for dep_nombre, dep_id in dep_ids.items():
        _get_or_create(
            conn,
            "SELECT id FROM plan_limpieza WHERE empresa_id = ? AND centro_id = ? AND departamento_id = ? AND tarea = 'Limpieza operativa diaria' LIMIT 1",
            (empresa_id, centro_id, dep_id),
            """
            INSERT INTO plan_limpieza
            (area, tarea, frecuencia, departamento_id, responsable, productos, instrucciones, activo, empresa_id, centro_id)
            VALUES (?, 'Limpieza operativa diaria', 'diaria', ?, 'Responsable departamento', 'Producto autorizado', 'Registrar limpieza, incidencias y verificacion.', 1, ?, ?)
            """,
            (dep_nombre, dep_id, empresa_id, centro_id),
        )
        _get_or_create(
            conn,
            "SELECT id FROM appcc_controles_plan WHERE empresa_id = ? AND centro_id = ? AND codigo = ? LIMIT 1",
            (empresa_id, centro_id, f"{dep_id}-limpieza-departamento"),
            """
            INSERT INTO appcc_controles_plan
            (empresa_id, centro_id, codigo, area, etapa, tipo_control, peligro, medida_preventiva, limite_critico, frecuencia, responsable, accion_correctiva, verificacion, modulo_origen, activo)
            VALUES (?, ?, ?, ?, 'Prerrequisito higiene', 'prerrequisito', 'Contaminacion cruzada o falta de higiene', 'Plan de limpieza con registro y responsable', 'Registro completo y zona apta', 'diaria', 'Responsable departamento', 'Repetir limpieza y registrar incidencia', 'Revision APPCC', 'setup_ia', 1)
            """,
            (empresa_id, centro_id, f"{dep_id}-limpieza-departamento", dep_nombre),
        )

    for codigo, area, etapa, tipo_control in [
        ("REC-01", "Recepcion mercancias", "Entrada proveedor", "prerrequisito"),
        ("FRIO-01", "Camaras y almacenes frios", "Conservacion", "pcc"),
        ("TRAZ-01", "Trazabilidad", "Lotes y movimientos", "prerrequisito"),
        ("INC-01", "Incidencias", "No conformidades", "prerrequisito"),
    ]:
        _get_or_create(
            conn,
            "SELECT id FROM appcc_controles_plan WHERE empresa_id = ? AND centro_id = ? AND codigo = ? LIMIT 1",
            (empresa_id, centro_id, codigo),
            """
            INSERT INTO appcc_controles_plan
            (empresa_id, centro_id, codigo, area, etapa, tipo_control, peligro, medida_preventiva, limite_critico, frecuencia, responsable, accion_correctiva, verificacion, modulo_origen, activo)
            VALUES (?, ?, ?, ?, ?, ?, 'Riesgo de seguridad alimentaria o perdida de trazabilidad', 'Registro, responsable y verificacion documentada', 'Evidencia completa y dentro de limites definidos', 'segun operacion', 'Responsable APPCC', 'Bloquear, corregir y registrar incidencia', 'Revision periodica', 'setup_ia', 1)
            """,
            (empresa_id, centro_id, codigo, area, etapa, tipo_control),
        )

    ensure_equipos_unificados_schema(conn)
    conn.commit()
    return centro_id

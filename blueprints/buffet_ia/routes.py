from datetime import date

from flask import flash, redirect, render_template, request, session, url_for

from database import get_db
from helpers.centros_helper import centro_activo_id
from blueprints.auth.routes import login_required, usuario_tiene_permiso
from . import buffet_ia_bp


def _column_exists(conn, table, column):
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def _add_column(conn, table, ddl):
    column = ddl.strip().split()[0]
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _empresa_id():
    return session.get("user_empresa_id", 1) or 1


def _norm(txt):
    return (txt or "").strip().lower()


def ensure_buffet_ia_schema(conn):
    _add_column(conn, 'menus_diarios', 'centro_id INTEGER')
    conn.execute("""
        CREATE TABLE IF NOT EXISTS buffet_lineas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            centro_id INTEGER,
            nombre TEXT NOT NULL,
            tipo TEXT,
            ubicacion TEXT,
            capacidad INTEGER DEFAULT 0,
            sentido_circulacion TEXT,
            temperatura TEXT,
            prioridad_visual INTEGER DEFAULT 0,
            activa INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS buffet_huecos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            linea_id INTEGER NOT NULL,
            posicion INTEGER DEFAULT 0,
            nombre TEXT,
            ancho REAL DEFAULT 0,
            profundidad REAL DEFAULT 0,
            altura REAL DEFAULT 0,
            temperatura TEXT,
            enchufe INTEGER DEFAULT 0,
            soporte_calor_frio TEXT,
            zona_visible INTEGER DEFAULT 0,
            prioridad_visual INTEGER DEFAULT 0,
            flujo_clientes TEXT,
            activa INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menaje_presentacion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            centro_id INTEGER,
            nombre TEXT NOT NULL,
            tipo TEXT,
            capacidad REAL DEFAULT 0,
            estetica TEXT,
            altura REAL DEFAULT 0,
            color TEXT,
            compatible_caliente INTEGER DEFAULT 0,
            compatible_frio INTEGER DEFAULT 0,
            compatible_appcc INTEGER DEFAULT 1,
            fotografia TEXT,
            disponibilidad INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reglas_montaje_buffet (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            centro_id INTEGER,
            nombre TEXT NOT NULL,
            tipo TEXT,
            condicion TEXT,
            accion TEXT,
            prioridad INTEGER DEFAULT 50,
            activa INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS montajes_buffet (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            centro_id INTEGER,
            fecha TEXT NOT NULL,
            servicio TEXT,
            menu_diario_id INTEGER,
            estado TEXT DEFAULT 'propuesto',
            resumen TEXT,
            alertas TEXT,
            empresa_id INTEGER DEFAULT 1,
            creado_por INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS montaje_buffet_lineas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            montaje_id INTEGER NOT NULL,
            receta_id INTEGER,
            plato TEXT,
            linea_id INTEGER,
            hueco_id INTEGER,
            menaje_id INTEGER,
            decoracion TEXT,
            orden_visual INTEGER DEFAULT 0,
            prioridad INTEGER DEFAULT 50,
            temperatura TEXT,
            reposicion TEXT,
            alerta_appcc TEXT,
            razon_ia TEXT,
            empresa_id INTEGER DEFAULT 1
        )
    """)
    for table in ("buffet_lineas", "menaje_presentacion", "reglas_montaje_buffet", "montajes_buffet"):
        _add_column(conn, table, "centro_id INTEGER")
        _add_column(conn, table, "empresa_id INTEGER DEFAULT 1")
    for ddl in [
        "score_visual REAL DEFAULT 0",
        "score_appcc REAL DEFAULT 0",
        "score_flujo REAL DEFAULT 0",
        "checklist TEXT",
        "plan_reposicion TEXT",
        "oportunidades_visuales TEXT",
        "contexto_fisico TEXT",
    ]:
        _add_column(conn, "montajes_buffet", ddl)
    for ddl in [
        "score_visual REAL DEFAULT 0",
        "score_appcc REAL DEFAULT 0",
        "score_flujo REAL DEFAULT 0",
        "fase_montaje TEXT",
        "equipo_responsable TEXT",
        "checklist TEXT",
    ]:
        _add_column(conn, "montaje_buffet_lineas", ddl)
    conn.commit()


def _seed_base(conn, empresa_id, centro_id):
    lineas = [
        ("Linea fria", "frio", "Entrada buffet", 12, "entrada->centro", "frio", 80),
        ("Linea caliente", "caliente", "Zona central", 14, "izquierda->derecha", "caliente", 90),
        ("Postres", "postres", "Final recorrido", 10, "final", "frio", 70),
        ("Panes y guarniciones", "neutro", "Inicio lateral", 8, "entrada", "ambiente", 45),
        ("Showcooking", "showcooking", "Punto visible", 6, "central", "caliente", 95),
        ("Healthy corner", "healthy", "Zona luminosa", 6, "inicio", "frio", 75),
    ]
    for nombre, tipo, ubicacion, capacidad, sentido, temperatura, prioridad in lineas:
        row = conn.execute("""
            SELECT id FROM buffet_lineas
            WHERE empresa_id = ? AND centro_id = ? AND LOWER(nombre) = LOWER(?)
        """, (empresa_id, centro_id, nombre)).fetchone()
        if row:
            linea_id = row["id"]
        else:
            cur = conn.execute("""
                INSERT INTO buffet_lineas
                (centro_id, nombre, tipo, ubicacion, capacidad, sentido_circulacion, temperatura, prioridad_visual, activa, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (centro_id, nombre, tipo, ubicacion, capacidad, sentido, temperatura, prioridad, empresa_id))
            linea_id = cur.lastrowid
        for pos in range(1, min(capacidad, 12) + 1):
            exists = conn.execute("""
                SELECT id FROM buffet_huecos
                WHERE empresa_id = ? AND linea_id = ? AND posicion = ?
            """, (empresa_id, linea_id, pos)).fetchone()
            if not exists:
                conn.execute("""
                    INSERT INTO buffet_huecos
                    (linea_id, posicion, nombre, ancho, profundidad, altura, temperatura, enchufe,
                     soporte_calor_frio, zona_visible, prioridad_visual, flujo_clientes, activa, empresa_id)
                    VALUES (?, ?, ?, 60, 40, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """, (
                    linea_id, pos, f"Hueco {pos}", 90 if pos in (1, 2, 3) else 20,
                    temperatura, 1 if temperatura == "caliente" else 0,
                    temperatura, 1 if pos in (1, 2, 3, 6) else 0,
                    max(20, prioridad - abs(pos - 4) * 5), "alto" if pos <= 4 else "medio", empresa_id,
                ))
    menajes = [
        ("GN inox 1/1", "gn", 1, "profesional", 0, "inox", 1, 1),
        ("GN inox 1/2", "gn", 0.5, "profesional", 0, "inox", 1, 1),
        ("Pizarra negra", "pizarra", 1, "premium", 0, "negro", 0, 1),
        ("Fuente madera", "fuente", 1, "rustico", 0, "madera", 0, 0),
        ("Bowl ceramica blanco", "bowl", 0.6, "limpio", 0, "blanco", 0, 1),
        ("Soporte altura", "altura", 0, "volumen", 25, "negro", 0, 1),
        ("Cazuela hierro", "cazuela", 1, "casero", 0, "negro", 1, 0),
    ]
    for item in menajes:
        if not conn.execute("SELECT id FROM menaje_presentacion WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER(?)", (empresa_id, centro_id, item[0])).fetchone():
            conn.execute("""
                INSERT INTO menaje_presentacion
                (centro_id, nombre, tipo, capacidad, estetica, altura, color, compatible_caliente, compatible_frio, compatible_appcc, disponibilidad, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
            """, (centro_id,) + item + (empresa_id,))
    reglas = [
        ("Postres al final", "flujo", "postre", "Asignar a linea postres y final de recorrido", 90),
        ("Caliente con calor", "appcc", "caliente", "Usar hueco caliente y menaje compatible calor", 95),
        ("Frio con frio", "appcc", "frio", "Usar linea fria y menaje compatible frio", 95),
        ("Plato estrella visible", "visual", "prioridad_alta", "Hueco visible o zona central premium", 80),
        ("Infantil baja altura", "flujo", "infantil", "Altura baja y acceso facil", 70),
        ("Salsas junto al plato", "operativa", "salsa", "Colocar junto al plato relacionado", 75),
        ("Alergenos separados", "appcc", "alergeno", "Evitar cruces y marcar alerta APPCC", 100),
    ]
    for regla in reglas:
        if not conn.execute("SELECT id FROM reglas_montaje_buffet WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER(?)", (empresa_id, centro_id, regla[0])).fetchone():
            conn.execute("""
                INSERT INTO reglas_montaje_buffet
                (centro_id, nombre, tipo, condicion, accion, prioridad, activa, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """, (centro_id,) + regla + (empresa_id,))
    conn.commit()


def _menus_disponibles(conn, empresa_id, centro_id):
    return conn.execute("""
        SELECT md.id, md.fecha, t.nombre AS tipo_nombre,
               COUNT(mp.id) AS platos
        FROM menus_diarios md
        JOIN tipos_menu t ON t.id = md.tipo_menu_id
        LEFT JOIN menu_subdepartamentos ms ON ms.menu_diario_id = md.id
        LEFT JOIN menu_platos mp ON mp.menu_subdepartamento_id = ms.id
        WHERE md.empresa_id = ?
          AND (? IS NULL OR md.centro_id = ?)
        GROUP BY md.id
        ORDER BY date(md.fecha) DESC, md.id DESC
        LIMIT 90
    """, (empresa_id, centro_id, centro_id)).fetchall()


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _count_like(conn, table, empresa_id, centro_id, columns):
    if not _table_exists(conn, table):
        return 0
    existing = [c for c in columns if _column_exists(conn, table, c)]
    if not existing:
        return 0
    where = " OR ".join([f"LOWER(COALESCE({c}, '')) LIKE '%buffet%'" for c in existing])
    empresa_filter = "empresa_id = ?" if _column_exists(conn, table, "empresa_id") else "1=1"
    centro_filter = "AND centro_id = ?" if _column_exists(conn, table, "centro_id") else ""
    params = [empresa_id] if empresa_filter != "1=1" else []
    if centro_filter:
        params.append(centro_id)
    return conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM {table}
        WHERE {empresa_filter}
          {centro_filter}
          AND ({where})
        """,
        params,
    ).fetchone()["total"]


def _rows_like(conn, table, empresa_id, centro_id, columns, limit=12):
    if not _table_exists(conn, table):
        return []
    existing = [c for c in columns if _column_exists(conn, table, c)]
    if not existing:
        return []
    terms = (
        "buffet", "buefft", "linea", "linea", "show", "frio", "frio",
        "caliente", "postre", "pan", "nevera", "camara", "camara"
    )
    filtros = []
    params = []
    for col in existing:
        for term in terms:
            filtros.append(f"LOWER(COALESCE({col}, '')) LIKE ?")
            params.append(f"%{term}%")
    empresa_filter = "empresa_id = ?" if _column_exists(conn, table, "empresa_id") else "1=1"
    centro_filter = "AND centro_id = ?" if _column_exists(conn, table, "centro_id") else ""
    final_params = [empresa_id] if empresa_filter != "1=1" else []
    if centro_filter:
        final_params.append(centro_id)
    final_params.extend(params)
    select_cols = [c for c in ("id", "nombre", "tipo", "ubicacion", "codigo", "responsable", "rango_min", "rango_max") if _column_exists(conn, table, c)]
    if not select_cols:
        select_cols = ["*"]
    return conn.execute(
        f"""
        SELECT {", ".join(select_cols)}
        FROM {table}
        WHERE {empresa_filter}
          {centro_filter}
          AND ({" OR ".join(filtros)})
        ORDER BY id DESC
        LIMIT ?
        """,
        final_params + [limit],
    ).fetchall()


def _count_inventario_buffet(conn, empresa_id, centro_id):
    if not _table_exists(conn, "inventario_articulos"):
        return 0
    where = [
        "ia.empresa_id = ?",
        "COALESCE(ia.activo, 1) = 1",
        "ia.centro_id = ?",
        "("
        "LOWER(COALESCE(ia.uso_operativo, '')) IN ('cliente_buffet','servicio_buffet','presentacion_buffet','cocina_utillaje','cocina_produccion') "
        "OR LOWER(COALESCE(ia.familia, '')) IN ('menaje','utillaje','vajilla','cristaleria','cuberteria','cocina','buffet') "
        "OR LOWER(COALESCE(ia.nombre, '')) LIKE '%buffet%' "
        "OR LOWER(COALESCE(ia.formato, '')) LIKE '%gn%'"
        ")",
    ]
    return conn.execute(
        f"SELECT COUNT(*) AS total FROM inventario_articulos ia WHERE {' AND '.join(where)}",
        (empresa_id, centro_id),
    ).fetchone()["total"]


def _inventario_buffet_rows(conn, empresa_id, centro_id, limit=18):
    if not _table_exists(conn, "inventario_articulos"):
        return []
    return conn.execute(
        """
        SELECT ia.id, ia.codigo, ia.nombre, ia.familia, ia.uso_operativo AS tipo,
               COALESCE(ia.formato, ia.capacidad, ia.material) AS ubicacion,
               COALESCE(SUM(ie.cantidad), 0) AS cantidad_total
        FROM inventario_articulos ia
        LEFT JOIN inventario_existencias ie ON ie.articulo_id = ia.id AND ie.empresa_id = ia.empresa_id
        WHERE ia.empresa_id = ?
          AND ia.centro_id = ?
          AND COALESCE(ia.activo, 1) = 1
          AND (
            LOWER(COALESCE(ia.uso_operativo, '')) IN ('cliente_buffet','servicio_buffet','presentacion_buffet','cocina_utillaje','cocina_produccion')
            OR LOWER(COALESCE(ia.familia, '')) IN ('menaje','utillaje','vajilla','cristaleria','cuberteria','cocina','buffet')
            OR LOWER(COALESCE(ia.nombre, '')) LIKE '%buffet%'
            OR LOWER(COALESCE(ia.formato, '')) LIKE '%gn%'
          )
        GROUP BY ia.id
        ORDER BY
          CASE COALESCE(ia.uso_operativo, '')
            WHEN 'servicio_buffet' THEN 1
            WHEN 'presentacion_buffet' THEN 2
            WHEN 'cliente_buffet' THEN 3
            WHEN 'cocina_utillaje' THEN 4
            ELSE 5
          END,
          ia.nombre
        LIMIT ?
        """,
        (empresa_id, centro_id, limit),
    ).fetchall()


def _contexto_fisico_buffet(conn, empresa_id, centro_id):
    piezas = []
    for table, label, columns in [
        ("almacenes", "almacenes/ubicaciones buffet", ("nombre", "ubicacion", "departamento")),
        ("equipos", "equipos APPCC/servicio buffet", ("nombre", "tipo", "ubicacion")),
        ("equipos_mantenimiento", "activos mantenimiento buffet", ("nombre", "tipo", "ubicacion", "familia")),
    ]:
        total = _count_like(conn, table, empresa_id, centro_id, columns)
        if total:
            piezas.append(f"{total} {label}")
    total_inventario = _count_inventario_buffet(conn, empresa_id, centro_id)
    if total_inventario:
        piezas.append(f"{total_inventario} articulos de menaje/utillaje operativo")
    if not piezas:
        return "Sin inventario fisico buffet detectado automaticamente; usando lineas base configurables."
    return "Contexto fisico detectado: " + ", ".join(piezas) + "."


def _activos_fisicos_buffet(conn, empresa_id, centro_id):
    def normalizar(row):
        data = dict(row)
        return {
            "id": data.get("id"),
            "nombre": data.get("nombre"),
            "tipo": data.get("tipo"),
            "ubicacion": data.get("ubicacion"),
            "codigo": data.get("codigo"),
            "responsable": data.get("responsable"),
            "rango_min": data.get("rango_min"),
            "rango_max": data.get("rango_max"),
            "cantidad_total": data.get("cantidad_total"),
        }

    bloques = []
    for table, label, columns in [
        ("almacenes", "Almacenes y camaras", ("nombre", "ubicacion", "tipo")),
        ("equipos", "Equipos APPCC / servicio", ("nombre", "ubicacion", "tipo", "codigo")),
        ("equipos_mantenimiento", "Activos mantenimiento", ("nombre", "ubicacion", "tipo", "codigo", "familia")),
    ]:
        rows = _rows_like(conn, table, empresa_id, centro_id, columns)
        if rows:
            bloques.append({"titulo": label, "tabla": table, "items": [normalizar(row) for row in rows]})
    inventario_rows = _inventario_buffet_rows(conn, empresa_id, centro_id)
    if inventario_rows:
        bloques.append({
            "titulo": "Menaje y utillaje operativo",
            "tabla": "inventario_articulos",
            "items": [normalizar(row) for row in inventario_rows],
        })
    return bloques


def _platos_menu(conn, menu_id):
    return conn.execute("""
        SELECT mp.id AS menu_plato_id, mp.orden, ms.seccion, sd.nombre AS subdepartamento,
               r.id AS receta_id, r.nombre AS plato, r.alergenos, r.categoria
        FROM menu_platos mp
        JOIN menu_subdepartamentos ms ON ms.id = mp.menu_subdepartamento_id
        LEFT JOIN subdepartamentos sd ON sd.id = ms.subdepartamento_id
        JOIN recetas r ON r.id = mp.receta_id
        WHERE ms.menu_diario_id = ?
        ORDER BY ms.orden, mp.orden, r.nombre
    """, (menu_id,)).fetchall()


def _clasificar_plato(plato):
    texto = " ".join([plato["plato"] or "", plato["seccion"] or "", plato["subdepartamento"] or "", plato["categoria"] or ""]).lower()
    nombre = (plato["plato"] or "").lower()
    bolleria = (
        "napolitana", "croissant", "ensaimada", "magdalena", "donut", "berlina",
        "bolleria", "palmera", "caracola", "hojaldre", "pan chocolate",
        "pain au chocolat",
    )
    if any(x in nombre for x in bolleria):
        return "panaderia"
    quesos_fiambres = (
        "queso", "edam", "gouda", "havarti", "cheddar", "chedar", "emmental",
        "emental", "roquefort", "fresco", "fiambre", "embutido", "jamon york",
        "jamon serrano", "mortadela", "salami", "chorizo", "pavo lonchas",
    )
    if any(x in nombre for x in quesos_fiambres) and not any(x in nombre for x in ("tarta", "quesadilla", "pizza", "salsa", "gratin")):
        return "frio"
    if "quesadilla" in nombre:
        return "caliente"
    if any(x in texto for x in ("postre", "tarta", "flan", "helado", "fruta", "natilla", "brownie")):
        return "postres"
    if any(x in texto for x in (
        "ensalada", "gazpacho", "frio", "sushi", "salpicon", "lechuga", "tomate",
        "cebolla", "zanahoria", "remolacha", "maiz", "pimiento", "espinaca",
        "col ", "pepino", "crudite", "vinagreta"
    )):
        return "frio"
    if any(x in texto for x in ("show", "plancha", "grill", "wok")):
        return "showcooking"
    if any(x in texto for x in ("pan", "arroz", "pasta", "patata", "guarnicion")):
        return "neutro"
    if any(x in texto for x in ("healthy", "vegetal", "vegano", "verdura")):
        return "healthy"
    return "caliente"


def _fase_montaje(tipo):
    if tipo == "panaderia":
        return "45-60 min antes del servicio"
    if tipo in ("frio", "healthy", "postres"):
        return "30-45 min antes del servicio"
    if tipo in ("caliente", "showcooking"):
        return "15-25 min antes del servicio"
    return "45-60 min antes del servicio"


def _equipo_responsable(tipo):
    if tipo == "panaderia":
        return "Desayunos / sala buffet"
    if tipo in ("caliente", "showcooking"):
        return "Cocina caliente / buffet"
    if tipo in ("frio", "healthy"):
        return "Cuarto frio / buffet"
    if tipo == "postres":
        return "Pasteleria / buffet"
    return "Sala buffet"


def _scores_linea(tipo, alergenos, linea, hueco, menaje):
    visual = 72
    appcc = 88
    flujo = 76
    if hueco and hueco["zona_visible"]:
        visual += 12
    if menaje and (menaje["estetica"] or "").lower() in {"premium", "volumen", "limpio"}:
        visual += 8
    if alergenos:
        appcc -= 10
    if tipo in ("caliente", "showcooking") and linea and (linea["temperatura"] or "").lower() == "caliente":
        appcc += 8
    if tipo in ("frio", "postres", "healthy") and linea and (linea["temperatura"] or "").lower() == "frio":
        appcc += 8
    if hueco and (hueco["flujo_clientes"] or "").lower() == "alto":
        flujo += 8
    return min(100, visual), min(100, appcc), min(100, flujo)


def _linea_para_tipo(lineas, tipo):
    preferidas = {
        "postres": ("postres",),
        "frio": ("frio", "healthy"),
        "showcooking": ("showcooking", "caliente"),
        "healthy": ("healthy", "frio"),
        "panaderia": ("neutro", "frio"),
        "neutro": ("neutro", "frio"),
        "caliente": ("caliente", "showcooking"),
    }
    for tipo_linea in preferidas.get(tipo, ("caliente",)):
        for linea in lineas:
            if (linea["tipo"] or "").lower() == tipo_linea:
                return linea
    return lineas[0] if lineas else None


def _menaje_para_tipo(menajes, tipo):
    def ok(m):
        if tipo in ("caliente", "showcooking"):
            return m["compatible_caliente"]
        if tipo in ("frio", "postres", "healthy"):
            return m["compatible_frio"]
        return True
    prefer = {
        "postres": ("pizarra", "bowl", "fuente"),
        "frio": ("pizarra", "bowl", "gn"),
        "healthy": ("bowl", "pizarra", "fuente"),
        "panaderia": ("fuente", "pizarra", "gn"),
        "caliente": ("gn", "cazuela"),
        "showcooking": ("cazuela", "gn"),
        "neutro": ("fuente", "gn"),
    }
    for tipo_menaje in prefer.get(tipo, ("gn",)):
        for menaje in menajes:
            if ok(menaje) and (menaje["tipo"] or "").lower() == tipo_menaje:
                return menaje
    return next((m for m in menajes if ok(m)), menajes[0] if menajes else None)


def _generar_montaje(conn, empresa_id, centro_id, menu_id, servicio):
    menu = conn.execute("SELECT * FROM menus_diarios WHERE id=? AND empresa_id=?", (menu_id, empresa_id)).fetchone()
    if not menu:
        return None
    platos = _platos_menu(conn, menu_id)
    lineas = conn.execute("""
        SELECT * FROM buffet_lineas
        WHERE empresa_id=? AND centro_id=? AND COALESCE(activa,1)=1
        ORDER BY prioridad_visual DESC, id
    """, (empresa_id, centro_id)).fetchall()
    menajes = conn.execute("""
        SELECT * FROM menaje_presentacion
        WHERE empresa_id=? AND centro_id=? AND COALESCE(disponibilidad,1)=1
        ORDER BY compatible_appcc DESC, altura DESC, id
    """, (empresa_id, centro_id)).fetchall()
    huecos_por_linea = {
        linea["id"]: conn.execute("""
            SELECT * FROM buffet_huecos
            WHERE empresa_id=? AND linea_id=? AND COALESCE(activa,1)=1
            ORDER BY prioridad_visual DESC, posicion
        """, (empresa_id, linea["id"])).fetchall()
        for linea in lineas
    }
    contexto_fisico = _contexto_fisico_buffet(conn, empresa_id, centro_id)
    cur = conn.execute("""
        INSERT INTO montajes_buffet
        (centro_id, fecha, servicio, menu_diario_id, estado, resumen, alertas, empresa_id, creado_por, contexto_fisico)
        VALUES (?, ?, ?, ?, 'propuesto', ?, '', ?, ?, ?)
    """, (centro_id, menu["fecha"], servicio, menu_id, f"Propuesta IA para {len(platos)} platos.", empresa_id, session.get("user_id"), contexto_fisico))
    montaje_id = cur.lastrowid
    usados = set()
    alertas = []
    visual_scores = []
    appcc_scores = []
    flujo_scores = []
    reposicion_alta = []
    oportunidades = []
    for idx, plato in enumerate(platos, start=1):
        tipo = _clasificar_plato(plato)
        linea = _linea_para_tipo(lineas, tipo)
        if not linea:
            continue
        huecos = huecos_por_linea.get(linea["id"], [])
        hueco = next((h for h in huecos if h["id"] not in usados), huecos[0] if huecos else None)
        if hueco:
            usados.add(hueco["id"])
        menaje = _menaje_para_tipo(menajes, tipo)
        alergenos = plato["alergenos"] or ""
        alerta = ""
        if alergenos:
            alerta = f"Separar y senalizar alergenos: {alergenos}"
            alertas.append(f"{plato['plato']}: {alerta}")
        reposicion = "alta" if tipo in ("caliente", "postres") else "media"
        if reposicion == "alta":
            reposicion_alta.append(plato["plato"])
        decoracion = "altura y color" if tipo in ("postres", "healthy") else "presentacion limpia"
        razon = f"Asignado a {linea['nombre']} por tipo {tipo}; prioridad visual y APPCC."
        visual, appcc, flujo = _scores_linea(tipo, alergenos, linea, hueco, menaje)
        visual_scores.append(visual)
        appcc_scores.append(appcc)
        flujo_scores.append(flujo)
        fase = _fase_montaje(tipo)
        responsable = _equipo_responsable(tipo)
        checklist = "Temperatura OK; cartel alergeno; menaje limpio; pinza propia; reposicion definida"
        if tipo in ("postres", "healthy") and visual < 90:
            oportunidades.append(f"Dar mas altura/color a {plato['plato']}.")
        conn.execute("""
            INSERT INTO montaje_buffet_lineas
            (montaje_id, receta_id, plato, linea_id, hueco_id, menaje_id, decoracion,
             orden_visual, prioridad, temperatura, reposicion, alerta_appcc, razon_ia, empresa_id,
             score_visual, score_appcc, score_flujo, fase_montaje, equipo_responsable, checklist)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            montaje_id, plato["receta_id"], plato["plato"], linea["id"], hueco["id"] if hueco else None,
            menaje["id"] if menaje else None, decoracion, idx, linea["prioridad_visual"] or 50,
            linea["temperatura"], reposicion, alerta, razon, empresa_id,
            visual, appcc, flujo, fase, responsable, checklist,
        ))
    avg = lambda values: round(sum(values) / len(values), 1) if values else 0
    checklist_global = "\n".join([
        "1. Validar limpieza y montaje de lineas antes de sacar comida.",
        "2. Revisar frio/calor operativo antes del servicio.",
        "3. Colocar carteleria y alergenos antes de abrir puertas.",
        "4. Separar pinzas por plato y reforzar alergenos.",
        "5. Revisar huecos premium y alturas visuales.",
        "6. Preparar reposicion de platos de alta rotacion.",
    ])
    plan_reposicion = "Reposicion alta: " + ", ".join(reposicion_alta[:25]) if reposicion_alta else "Reposicion media general; sin platos criticos detectados."
    conn.execute("""
        UPDATE montajes_buffet
        SET alertas=?,
            score_visual=?,
            score_appcc=?,
            score_flujo=?,
            checklist=?,
            plan_reposicion=?,
            oportunidades_visuales=?
        WHERE id=?
    """, (
        "\n".join(alertas[:30]),
        avg(visual_scores),
        avg(appcc_scores),
        avg(flujo_scores),
        checklist_global,
        plan_reposicion,
        "\n".join(oportunidades[:20]),
        montaje_id,
    ))
    conn.commit()
    return montaje_id


@buffet_ia_bp.before_request
def _bloquear_sin_permiso():
    if "user_id" not in session:
        return None
    if usuario_tiene_permiso("produccion_ver") or usuario_tiene_permiso("produccion_editar") or usuario_tiene_permiso("appcc_ver") or usuario_tiene_permiso("acceso_global"):
        return None
    flash("No tienes permiso para IA de buffet.", "warning")
    return redirect(url_for("auth.login"))


@buffet_ia_bp.route("/", methods=["GET", "POST"])
@login_required
def panel():
    conn = get_db()
    ensure_buffet_ia_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    if not centro_id:
        conn.close()
        flash("Selecciona un centro antes de trabajar el montaje de buffet.", "warning")
        return redirect(url_for("menus.lista_menus_principal"))
    _seed_base(conn, empresa_id, centro_id)
    if request.method == "POST":
        menu_id = request.form.get("menu_diario_id")
        servicio = request.form.get("servicio") or "buffet"
        if not menu_id:
            flash("Selecciona un menu diario.", "warning")
            return redirect(url_for("buffet_ia.panel"))
        montaje_id = _generar_montaje(conn, empresa_id, centro_id, int(menu_id), servicio)
        conn.close()
        if montaje_id:
            flash("Montaje de buffet propuesto.", "success")
            return redirect(url_for("buffet_ia.ver_montaje", montaje_id=montaje_id))
        flash("No se pudo generar el montaje.", "warning")
        return redirect(url_for("buffet_ia.panel"))

    menus = _menus_disponibles(conn, empresa_id, centro_id)
    montajes = conn.execute("""
        SELECT m.*, md.fecha, t.nombre AS tipo_menu
        FROM montajes_buffet m
        LEFT JOIN menus_diarios md ON md.id = m.menu_diario_id
        LEFT JOIN tipos_menu t ON t.id = md.tipo_menu_id
        WHERE m.empresa_id=? AND m.centro_id=?
        ORDER BY m.id DESC
        LIMIT 20
    """, (empresa_id, centro_id)).fetchall()
    lineas = conn.execute("SELECT * FROM buffet_lineas WHERE empresa_id=? AND centro_id=? ORDER BY prioridad_visual DESC", (empresa_id, centro_id)).fetchall()
    menajes = conn.execute("SELECT * FROM menaje_presentacion WHERE empresa_id=? AND centro_id=? ORDER BY tipo,nombre", (empresa_id, centro_id)).fetchall()
    contexto_fisico = _contexto_fisico_buffet(conn, empresa_id, centro_id)
    activos_fisicos = _activos_fisicos_buffet(conn, empresa_id, centro_id)
    conn.close()
    return render_template(
        "buffet_ia/panel.html",
        menus=menus,
        montajes=montajes,
        lineas=lineas,
        menajes=menajes,
        contexto_fisico=contexto_fisico,
        activos_fisicos=activos_fisicos,
        hoy=date.today().isoformat(),
    )


@buffet_ia_bp.route("/montajes/<int:montaje_id>")
@login_required
def ver_montaje(montaje_id):
    conn = get_db()
    ensure_buffet_ia_schema(conn)
    empresa_id = _empresa_id()
    montaje = conn.execute("""
        SELECT m.*, ct.nombre AS centro_nombre, md.fecha, t.nombre AS tipo_menu
        FROM montajes_buffet m
        LEFT JOIN centros_trabajo ct ON ct.id = m.centro_id
        LEFT JOIN menus_diarios md ON md.id = m.menu_diario_id
        LEFT JOIN tipos_menu t ON t.id = md.tipo_menu_id
        WHERE m.id=? AND m.empresa_id=?
    """, (montaje_id, empresa_id)).fetchone()
    if not montaje:
        conn.close()
        flash("Montaje no encontrado.", "warning")
        return redirect(url_for("buffet_ia.panel"))
    detalle = conn.execute("""
        SELECT ml.*, bl.nombre AS linea, bl.tipo AS linea_tipo, bh.nombre AS hueco, bh.posicion,
               mp.nombre AS menaje, mp.tipo AS menaje_tipo
        FROM montaje_buffet_lineas ml
        LEFT JOIN buffet_lineas bl ON bl.id = ml.linea_id
        LEFT JOIN buffet_huecos bh ON bh.id = ml.hueco_id
        LEFT JOIN menaje_presentacion mp ON mp.id = ml.menaje_id
        WHERE ml.montaje_id=? AND ml.empresa_id=?
        ORDER BY bl.prioridad_visual DESC, bh.posicion, ml.orden_visual
    """, (montaje_id, empresa_id)).fetchall()
    conn.close()
    return render_template("buffet_ia/montaje.html", montaje=montaje, detalle=detalle)

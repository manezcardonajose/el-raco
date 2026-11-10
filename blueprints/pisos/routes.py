import unicodedata
from datetime import date, datetime
from functools import wraps

from flask import abort, flash, has_request_context, redirect, render_template, request, session, url_for

from database import get_db
from blueprints.auth.routes import login_required
from helpers.operativa_helper import ensure_incidencias_context_schema, ensure_mantenimiento_context_schema
from . import pisos_bp


TIPOS_PARTE = {
    "salida": "Salida",
    "entrada": "Entrada",
    "estancia": "Estancia",
    "repaso": "Repaso",
    "mantenimiento": "Mantenimiento",
}

ESTADOS_PARTE = {
    "pendiente": "Pendiente",
    "en_curso": "En curso",
    "hecha": "Hecha",
    "incidencia": "Incidencia",
    "cancelada": "Cancelada",
}

ESTADOS_HABITACION = {
    "disponible": "Disponible",
    "ocupada": "Ocupada",
    "limpieza": "Limpieza",
    "repaso": "Repaso",
    "mantenimiento": "Mantenimiento",
    "bloqueada": "Bloqueada",
}

TAREAS_PISOS = {
    "salida": {"nombre": "Limpieza salida", "categoria": "habitacion", "prioridad": "alta"},
    "entrada": {"nombre": "Preparacion entrada", "categoria": "habitacion", "prioridad": "alta"},
    "repaso": {"nombre": "Repaso estancia", "categoria": "habitacion", "prioridad": "media"},
    "toallas": {"nombre": "Cambio toallas", "categoria": "lenceria", "prioridad": "media"},
    "sabanas": {"nombre": "Cambio sabanas", "categoria": "lenceria", "prioridad": "media"},
    "bano_desinfeccion": {"nombre": "Desinfeccion bano", "categoria": "bano", "prioridad": "alta"},
    "amenities": {"nombre": "Reposicion amenities", "categoria": "habitacion", "prioridad": "baja"},
    "zona_comun": {"nombre": "Limpieza zona comun", "categoria": "zona", "prioridad": "media"},
    "mantenimiento": {"nombre": "Aviso mantenimiento", "categoria": "mantenimiento", "prioridad": "alta"},
}

TIPOS_ZONA = {
    "recepcion": "Recepcion",
    "bano_publico": "Bano publico",
    "duchas_piscina": "Duchas piscina",
    "pasillo": "Pasillo",
    "oficina": "Oficina",
    "restaurante_bar": "Restaurante / bar",
    "vestuario": "Vestuario",
    "gimnasio": "Gimnasio",
    "spa": "Spa",
    "parking": "Parking",
    "ascensor": "Ascensor",
    "escalera": "Escalera",
    "terraza": "Terraza",
    "piscina": "Piscina",
    "almacen": "Almacen",
    "lavanderia": "Lavanderia",
    "zona_comun": "Zona comun",
}

TIPOS_CAMA = {
    "": "Sin definir",
    "matrimonio": "Matrimonio",
    "dos_juntas": "2 camas juntas",
    "dos_separadas": "2 camas separadas",
    "individual": "Individual",
    "sofa_cama": "Sofa cama",
    "mixto": "Mixto",
}

TIPOS_AIRE = {
    "": "Sin definir",
    "convencional": "Equipo convencional",
    "fan_coil": "Fan coil",
    "split": "Split",
    "centralizado": "Centralizado",
    "sin_aire": "Sin aire",
}

TIPOS_INCIDENCIA_PISOS = {
    "mantenimiento": "Mantenimiento",
    "limpieza": "Limpieza",
    "rotura": "Rotura",
    "falta_material": "Falta material",
    "objeto_perdido": "Objeto perdido",
    "minibar": "Minibar",
    "lenceria_danada": "Lenceria danada",
    "seguridad": "Seguridad",
    "otro": "Otro",
}

INCIDENCIAS_A_MANTENIMIENTO_PISOS = {"mantenimiento", "rotura", "seguridad"}
TAREAS_REGISTRO_LIMPIEZA_PISOS = {"salida", "bano_desinfeccion", "zona_comun"}

CATEGORIAS_PRODUCTOS_PISOS = {
    "amenities": "Amenities",
    "quimico": "Quimicos limpieza",
    "papel": "Papel y desechables",
    "textil": "Textil / lenceria",
    "utensilio": "Utiles limpieza",
    "otro": "Otros",
}

ESTADOS_OBJETO_PERDIDO = {
    "guardado": "Guardado",
    "reclamado": "Reclamado",
    "entregado": "Entregado",
    "desechado": "Desechado",
}

MODOS_LAVANDERIA = {
    "propia": "Lavanderia propia",
    "externa": "Lavanderia externa",
    "mixta": "Propia y externa",
    "sin_control": "Sin control de lavanderia",
}

CIRCUITOS_LAVANDERIA = {
    "propia": "Propia",
    "externa": "Externa",
}

ESTADOS_LAVANDERIA = {
    "pendiente": "Pendiente",
    "preparada": "Preparada",
    "enviada": "Enviada",
    "recibida": "Recibida",
    "validada": "Validada",
    "incidencia": "Incidencia",
}

GRAVEDADES_INCIDENCIA = {
    "baja": "Baja",
    "media": "Media",
    "alta": "Alta",
    "urgente": "Urgente",
}

ZONAS_BASE_PISOS = [
    ("Recepcion", "recepcion", "", "0", "diaria", "alta", "Desinfectante superficies", "Mostrador, zona espera y papeleras."),
    ("Banos publicos", "bano_publico", "", "0", "diaria", "alta", "Desinfectante banos", "Lavabos, inodoros, pomos, espejos y reposicion."),
    ("Duchas piscina", "duchas_piscina", "", "0", "diaria", "alta", "Desinfectante banos", "Duchas, desagues, suelos y papeleras."),
    ("Pasillos", "pasillo", "", "", "diaria", "media", "Limpiador suelos", "Suelos, esquinas y puntos de contacto."),
    ("Oficinas direccion", "oficina", "", "", "diaria", "media", "Desinfectante superficies", "Mesas, papeleras y suelo."),
    ("Suelos restaurantes y bares", "restaurante_bar", "", "", "diaria", "alta", "Limpiador suelos alimentario", "Suelos de puntos de venta y zonas de paso."),
    ("Vestuario personal", "vestuario", "", "0", "diaria", "alta", "Desinfectante superficies", "Taquillas, bancos, duchas y papeleras."),
    ("Ascensores", "ascensor", "", "", "diaria", "media", "Desinfectante superficies", "Botoneras, espejos, guias y suelo."),
    ("Escaleras", "escalera", "", "", "diaria", "media", "Limpiador suelos", "Barandillas, rellanos y suelo."),
    ("Zona piscina", "piscina", "", "0", "diaria", "alta", "Desinfectante superficies", "Hamacas, papeleras, suelos y puntos de contacto."),
]


def _empresa_id():
    if not has_request_context():
        return 1
    return session.get("user_empresa_id", 1) or 1


def _normalizar_rol(valor):
    texto = str(valor or "").strip().lower()
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


ROLES_PISOS_GESTION = {
    "admin",
    "gerente",
    "direccion",
    "rrhh",
    "jefe_departamento",
    "segundo_jefe",
    "gobernanta",
    "supervisora",
    "jefe_pisos",
    "pisos_jefe",
}

ROLES_PISOS_OPERACION = ROLES_PISOS_GESTION | {
    "pisos",
    "camarera",
    "camarero",
    "limpieza",
    "lavanderia",
    "lenceria",
    "tablet_pisos",
}


def _roles_usuario():
    roles = {
        _normalizar_rol(session.get("user_rol")),
        _normalizar_rol(session.get("user_empleado_rol")),
    }
    return {rol for rol in roles if rol}


def _puede_pisos_gestionar():
    return bool(session.get("user_es_global") in (1, True, "1", "true", "True") or _roles_usuario() & ROLES_PISOS_GESTION)


def _puede_pisos():
    return bool(_puede_pisos_gestionar() or _roles_usuario() & ROLES_PISOS_OPERACION)


def _require_pisos(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _puede_pisos():
            flash("No tienes permisos para acceder a pisos.", "warning")
            return redirect(url_for("home"))
        return func(*args, **kwargs)

    return wrapper


def _require_pisos_gestion(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _puede_pisos_gestionar():
            flash("Solo gobernanta, jefatura o administracion pueden gestionar pisos.", "warning")
            return redirect(url_for("pisos.tablet"))
        return func(*args, **kwargs)

    return wrapper


def _parse_date(value, default=None):
    if not value:
        return default
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return default


def _to_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _to_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _form_bool(name, default=False):
    valores = request.form.getlist(name)
    if not valores:
        return default
    return any(str(valor).lower() in {"1", "true", "on", "si", "yes"} for valor in valores)


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _column_exists(conn, table_name, column_name):
    try:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return False
    return any(col["name"] == column_name or col[1] == column_name for col in cols)


def _add_column_if_missing(conn, table_name, column_sql):
    column_name = column_sql.strip().split()[0]
    if _table_exists(conn, table_name) and not _column_exists(conn, table_name, column_name):
        try:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
        except Exception as exc:
            if "non-constant default" not in str(exc).lower():
                raise
            safe_column_sql = column_sql.replace(" DEFAULT CURRENT_TIMESTAMP", "")
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {safe_column_sql}")


def _ensure_pisos_schema(conn):
    from blueprints.recepcion.routes import _ensure_recepcion_schema

    _ensure_recepcion_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_partes_limpieza (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            habitacion_id INTEGER NOT NULL,
            reserva_id INTEGER,
            tipo TEXT DEFAULT 'repaso',
            estado TEXT DEFAULT 'pendiente',
            prioridad TEXT DEFAULT 'media',
            asignado_a TEXT,
            observaciones TEXT,
            resultado TEXT,
            productos_usados TEXT,
            appcc_registro_id INTEGER,
            incidencia_id INTEGER,
            mantenimiento_orden_id INTEGER,
            creado_por INTEGER,
            cerrado_por INTEGER,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            iniciado_at TEXT,
            cerrado_at TEXT,
            updated_at TEXT
        )
        """
    )
    for col in [
        "fecha TEXT",
        "habitacion_id INTEGER",
        "reserva_id INTEGER",
        "tipo TEXT DEFAULT 'repaso'",
        "estado TEXT DEFAULT 'pendiente'",
        "prioridad TEXT DEFAULT 'media'",
        "asignado_a TEXT",
        "observaciones TEXT",
        "resultado TEXT",
        "productos_usados TEXT",
        "appcc_registro_id INTEGER",
        "incidencia_id INTEGER",
        "mantenimiento_orden_id INTEGER",
        "creado_por INTEGER",
        "cerrado_por INTEGER",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "iniciado_at TEXT",
        "cerrado_at TEXT",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "pisos_partes_limpieza", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_trazabilidad (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entidad TEXT NOT NULL,
            entidad_id INTEGER,
            accion TEXT NOT NULL,
            estado_anterior TEXT,
            estado_nuevo TEXT,
            detalle TEXT,
            usuario_id INTEGER,
            usuario_nombre TEXT,
            ip TEXT,
            user_agent TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "entidad TEXT",
        "entidad_id INTEGER",
        "accion TEXT",
        "estado_anterior TEXT",
        "estado_nuevo TEXT",
        "detalle TEXT",
        "usuario_id INTEGER",
        "usuario_nombre TEXT",
        "ip TEXT",
        "user_agent TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "pisos_trazabilidad", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_configuracion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repaso_diario INTEGER DEFAULT 1,
            cambio_toallas_cada_dias INTEGER DEFAULT 1,
            cambio_sabanas_cada_dias INTEGER DEFAULT 3,
            desinfeccion_bano_diaria INTEGER DEFAULT 1,
            amenities_diarios INTEGER DEFAULT 1,
            modo_lavanderia TEXT DEFAULT 'mixta',
            lavanderia_propia_nombre TEXT,
            lavanderia_externa_principal TEXT,
            empresa_id INTEGER DEFAULT 1,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "repaso_diario INTEGER DEFAULT 1",
        "cambio_toallas_cada_dias INTEGER DEFAULT 1",
        "cambio_sabanas_cada_dias INTEGER DEFAULT 3",
        "desinfeccion_bano_diaria INTEGER DEFAULT 1",
        "amenities_diarios INTEGER DEFAULT 1",
        "modo_lavanderia TEXT DEFAULT 'mixta'",
        "lavanderia_propia_nombre TEXT",
        "lavanderia_externa_principal TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "updated_at TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "pisos_configuracion", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_zonas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo TEXT DEFAULT 'zona_comun',
            bloque TEXT,
            planta TEXT,
            frecuencia TEXT DEFAULT 'diaria',
            prioridad TEXT DEFAULT 'media',
            productos_previstos TEXT,
            instrucciones TEXT,
            appcc_critico INTEGER DEFAULT 1,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "nombre TEXT",
        "tipo TEXT DEFAULT 'zona_comun'",
        "bloque TEXT",
        "planta TEXT",
        "frecuencia TEXT DEFAULT 'diaria'",
        "prioridad TEXT DEFAULT 'media'",
        "productos_previstos TEXT",
        "instrucciones TEXT",
        "appcc_critico INTEGER DEFAULT 1",
        "departamento_id INTEGER",
        "subdepartamento_id INTEGER",
        "punto_venta_id INTEGER",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "pisos_zonas", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_trabajos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            origen TEXT DEFAULT 'regla',
            target_tipo TEXT DEFAULT 'habitacion',
            habitacion_id INTEGER,
            zona_id INTEGER,
            reserva_id INTEGER,
            tarea_codigo TEXT NOT NULL,
            tarea_nombre TEXT NOT NULL,
            categoria TEXT DEFAULT 'habitacion',
            estado TEXT DEFAULT 'pendiente',
            prioridad TEXT DEFAULT 'media',
            bloque TEXT,
            planta TEXT,
            asignado_a TEXT,
            asignado_empleado_id INTEGER,
            productos_previstos TEXT,
            productos_usados TEXT,
            sabanas_previstas INTEGER DEFAULT 0,
            toallas_previstas INTEGER DEFAULT 0,
            alfombrines_previstos INTEGER DEFAULT 0,
            sabanas_usadas INTEGER DEFAULT 0,
            toallas_usadas INTEGER DEFAULT 0,
            alfombrines_usados INTEGER DEFAULT 0,
            lavanderia_lote TEXT,
            proveedor_lavanderia TEXT,
            productos_lavado TEXT,
            checklist TEXT,
            observaciones TEXT,
            resultado TEXT,
            incidencia_tipo TEXT,
            incidencia_gravedad TEXT,
            appcc_registro_id INTEGER,
            incidencia_id INTEGER,
            mantenimiento_orden_id INTEGER,
            revision_estado TEXT DEFAULT 'pendiente',
            revisado_por INTEGER,
            revisado_at TEXT,
            revision_observaciones TEXT,
            creado_por INTEGER,
            cerrado_por INTEGER,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            iniciado_at TEXT,
            cerrado_at TEXT,
            updated_at TEXT
        )
        """
    )
    for col in [
        "fecha TEXT",
        "origen TEXT DEFAULT 'regla'",
        "target_tipo TEXT DEFAULT 'habitacion'",
        "habitacion_id INTEGER",
        "zona_id INTEGER",
        "reserva_id INTEGER",
        "tarea_codigo TEXT",
        "tarea_nombre TEXT",
        "categoria TEXT DEFAULT 'habitacion'",
        "estado TEXT DEFAULT 'pendiente'",
        "prioridad TEXT DEFAULT 'media'",
        "bloque TEXT",
        "planta TEXT",
        "asignado_a TEXT",
        "asignado_empleado_id INTEGER",
        "productos_previstos TEXT",
        "productos_usados TEXT",
        "sabanas_previstas INTEGER DEFAULT 0",
        "toallas_previstas INTEGER DEFAULT 0",
        "alfombrines_previstos INTEGER DEFAULT 0",
        "sabanas_usadas INTEGER DEFAULT 0",
        "toallas_usadas INTEGER DEFAULT 0",
        "alfombrines_usados INTEGER DEFAULT 0",
        "lavanderia_lote TEXT",
        "proveedor_lavanderia TEXT",
        "productos_lavado TEXT",
        "checklist TEXT",
        "observaciones TEXT",
        "resultado TEXT",
        "incidencia_tipo TEXT",
        "incidencia_gravedad TEXT",
        "appcc_registro_id INTEGER",
        "incidencia_id INTEGER",
        "mantenimiento_orden_id INTEGER",
        "revision_estado TEXT DEFAULT 'pendiente'",
        "revisado_por INTEGER",
        "revisado_at TEXT",
        "revision_observaciones TEXT",
        "creado_por INTEGER",
        "cerrado_por INTEGER",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "iniciado_at TEXT",
        "cerrado_at TEXT",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "pisos_trabajos", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_personal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER,
            nombre TEXT NOT NULL,
            rol TEXT DEFAULT 'camarera',
            telefono TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "empleado_id INTEGER",
        "nombre TEXT",
        "rol TEXT DEFAULT 'camarera'",
        "telefono TEXT",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "pisos_personal", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_lenceria_movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trabajo_id INTEGER,
            fecha TEXT NOT NULL,
            habitacion_id INTEGER,
            reserva_id INTEGER,
            tipo TEXT DEFAULT 'registro',
            sabanas INTEGER DEFAULT 0,
            toallas INTEGER DEFAULT 0,
            alfombrines INTEGER DEFAULT 0,
            lote_lavanderia TEXT,
            proveedor_lavanderia TEXT,
            circuito_lavanderia TEXT DEFAULT 'propia',
            lavanderia_proveedor_id INTEGER,
            estado_lavanderia TEXT DEFAULT 'pendiente',
            fecha_salida TEXT,
            fecha_retorno_prevista TEXT,
            fecha_retorno TEXT,
            kg_estimados REAL DEFAULT 0,
            coste_estimado REAL DEFAULT 0,
            coste_real REAL DEFAULT 0,
            albaran_lavanderia TEXT,
            lote_retorno TEXT,
            validado_por INTEGER,
            validado_at TEXT,
            productos_lavado TEXT,
            observaciones TEXT,
            usuario TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_registro TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "trabajo_id INTEGER",
        "fecha TEXT",
        "habitacion_id INTEGER",
        "reserva_id INTEGER",
        "tipo TEXT DEFAULT 'registro'",
        "sabanas INTEGER DEFAULT 0",
        "toallas INTEGER DEFAULT 0",
        "alfombrines INTEGER DEFAULT 0",
        "lote_lavanderia TEXT",
        "proveedor_lavanderia TEXT",
        "circuito_lavanderia TEXT DEFAULT 'propia'",
        "lavanderia_proveedor_id INTEGER",
        "estado_lavanderia TEXT DEFAULT 'pendiente'",
        "fecha_salida TEXT",
        "fecha_retorno_prevista TEXT",
        "fecha_retorno TEXT",
        "kg_estimados REAL DEFAULT 0",
        "coste_estimado REAL DEFAULT 0",
        "coste_real REAL DEFAULT 0",
        "albaran_lavanderia TEXT",
        "lote_retorno TEXT",
        "validado_por INTEGER",
        "validado_at TEXT",
        "productos_lavado TEXT",
        "observaciones TEXT",
        "usuario TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_registro TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "pisos_lenceria_movimientos", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_lavanderias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo TEXT DEFAULT 'externa',
            contacto TEXT,
            telefono TEXT,
            email TEXT,
            direccion TEXT,
            productos_lavado TEXT,
            instrucciones TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "nombre TEXT",
        "tipo TEXT DEFAULT 'externa'",
        "contacto TEXT",
        "telefono TEXT",
        "email TEXT",
        "direccion TEXT",
        "productos_lavado TEXT",
        "instrucciones TEXT",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "pisos_lavanderias", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER,
            nombre TEXT NOT NULL,
            categoria TEXT DEFAULT 'otro',
            unidad TEXT DEFAULT 'ud',
            stock_actual REAL DEFAULT 0,
            stock_minimo REAL DEFAULT 0,
            proveedor TEXT,
            ubicacion TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "producto_id INTEGER",
        "nombre TEXT",
        "categoria TEXT DEFAULT 'otro'",
        "unidad TEXT DEFAULT 'ud'",
        "stock_actual REAL DEFAULT 0",
        "stock_minimo REAL DEFAULT 0",
        "proveedor TEXT",
        "ubicacion TEXT",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "pisos_productos", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_consumos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trabajo_id INTEGER,
            fecha TEXT NOT NULL,
            producto_pisos_id INTEGER,
            producto_id INTEGER,
            producto_nombre TEXT NOT NULL,
            categoria TEXT,
            cantidad REAL DEFAULT 0,
            unidad TEXT,
            habitacion_id INTEGER,
            zona_id INTEGER,
            observaciones TEXT,
            usuario TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_registro TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "trabajo_id INTEGER",
        "fecha TEXT",
        "producto_pisos_id INTEGER",
        "producto_id INTEGER",
        "producto_nombre TEXT",
        "categoria TEXT",
        "cantidad REAL DEFAULT 0",
        "unidad TEXT",
        "habitacion_id INTEGER",
        "zona_id INTEGER",
        "observaciones TEXT",
        "usuario TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_registro TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "pisos_consumos", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_pedidos_internos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT DEFAULT CURRENT_DATE,
            estado TEXT DEFAULT 'pendiente',
            prioridad TEXT DEFAULT 'media',
            solicitante TEXT,
            observaciones TEXT,
            compra_pedido_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "fecha TEXT DEFAULT CURRENT_DATE",
        "estado TEXT DEFAULT 'pendiente'",
        "prioridad TEXT DEFAULT 'media'",
        "solicitante TEXT",
        "observaciones TEXT",
        "compra_pedido_id INTEGER",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "pisos_pedidos_internos", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_pedidos_lineas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL,
            producto_pisos_id INTEGER,
            producto_id INTEGER,
            nombre TEXT NOT NULL,
            cantidad REAL DEFAULT 0,
            unidad TEXT DEFAULT 'ud',
            estado TEXT DEFAULT 'pendiente',
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "pedido_id INTEGER",
        "producto_pisos_id INTEGER",
        "producto_id INTEGER",
        "nombre TEXT",
        "cantidad REAL DEFAULT 0",
        "unidad TEXT DEFAULT 'ud'",
        "estado TEXT DEFAULT 'pendiente'",
        "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "pisos_pedidos_lineas", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pisos_objetos_perdidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT DEFAULT CURRENT_DATE,
            habitacion_id INTEGER,
            reserva_id INTEGER,
            descripcion TEXT NOT NULL,
            categoria TEXT,
            encontrado_por TEXT,
            ubicacion_guardado TEXT,
            estado TEXT DEFAULT 'guardado',
            entregado_a TEXT,
            documento_entrega TEXT,
            fecha_entrega TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "fecha TEXT DEFAULT CURRENT_DATE",
        "habitacion_id INTEGER",
        "reserva_id INTEGER",
        "descripcion TEXT",
        "categoria TEXT",
        "encontrado_por TEXT",
        "ubicacion_guardado TEXT",
        "estado TEXT DEFAULT 'guardado'",
        "entregado_a TEXT",
        "documento_entrega TEXT",
        "fecha_entrega TEXT",
        "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "pisos_objetos_perdidos", col)
    _seed_pisos_basico(conn)
    _seed_lavanderias_basico(conn)
    _seed_productos_pisos_basico(conn)
    _ensure_incidencias_min_schema(conn)
    ensure_incidencias_context_schema(conn)
    _ensure_appcc_limpieza_schema(conn)
    _ensure_mantenimiento_min_schema(conn)
    ensure_mantenimiento_context_schema(conn)
    conn.commit()


def _ensure_appcc_limpieza_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_limpieza (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area TEXT NOT NULL,
            tarea TEXT NOT NULL,
            frecuencia TEXT DEFAULT 'diaria',
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            responsable TEXT,
            productos TEXT,
            instrucciones TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "area TEXT",
        "tarea TEXT",
        "frecuencia TEXT DEFAULT 'diaria'",
        "departamento_id INTEGER",
        "subdepartamento_id INTEGER",
        "punto_venta_id INTEGER",
        "responsable TEXT",
        "productos TEXT",
        "instrucciones TEXT",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "plan_limpieza", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registros_limpieza (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP,
            realizada_por TEXT NOT NULL,
            observaciones TEXT,
            incidencia_id INTEGER,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "plan_id INTEGER",
        "fecha TEXT DEFAULT CURRENT_TIMESTAMP",
        "realizada_por TEXT",
        "observaciones TEXT",
        "incidencia_id INTEGER",
        "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "registros_limpieza", col)


def _ensure_incidencias_min_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS incidencias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            referencia_id INTEGER,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP,
            usuario TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            gravedad TEXT DEFAULT 'media',
            estado TEXT DEFAULT 'abierta',
            solucion TEXT,
            fecha_resolucion TEXT,
            resuelto_por TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "tipo TEXT",
        "referencia_id INTEGER",
        "fecha TEXT DEFAULT CURRENT_TIMESTAMP",
        "usuario TEXT",
        "descripcion TEXT",
        "gravedad TEXT DEFAULT 'media'",
        "estado TEXT DEFAULT 'abierta'",
        "solucion TEXT",
        "fecha_resolucion TEXT",
        "resuelto_por TEXT",
        "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "incidencias", col)


def _ensure_mantenimiento_min_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS equipos_mantenimiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo TEXT NOT NULL,
            marca TEXT,
            modelo TEXT,
            numero_serie TEXT,
            fecha_compra TEXT,
            fecha_instalacion TEXT,
            garantia_meses INTEGER,
            proveedor_id INTEGER,
            ubicacion TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            manual_pdf TEXT,
            foto TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "nombre TEXT",
        "tipo TEXT",
        "marca TEXT",
        "modelo TEXT",
        "numero_serie TEXT",
        "fecha_compra TEXT",
        "fecha_instalacion TEXT",
        "garantia_meses INTEGER",
        "proveedor_id INTEGER",
        "ubicacion TEXT",
        "departamento_id INTEGER",
        "subdepartamento_id INTEGER",
        "punto_venta_id INTEGER",
        "manual_pdf TEXT",
        "foto TEXT",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "equipos_mantenimiento", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ordenes_trabajo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_orden TEXT UNIQUE NOT NULL,
            equipo_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            prioridad TEXT NOT NULL,
            estado TEXT NOT NULL,
            fecha_apertura TEXT DEFAULT CURRENT_TIMESTAMP,
            fecha_asignacion TEXT,
            fecha_inicio TEXT,
            fecha_fin TEXT,
            descripcion TEXT NOT NULL,
            tecnico_asignado TEXT,
            horas_estimadas REAL,
            horas_reales REAL,
            coste_estimado REAL,
            coste_real REAL,
            observaciones TEXT,
            incidencia_id INTEGER,
            cerrado_por TEXT,
            fecha_cierre TEXT,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "numero_orden TEXT",
        "equipo_id INTEGER",
        "tipo TEXT",
        "prioridad TEXT",
        "estado TEXT",
        "fecha_apertura TEXT DEFAULT CURRENT_TIMESTAMP",
        "fecha_asignacion TEXT",
        "fecha_inicio TEXT",
        "fecha_fin TEXT",
        "descripcion TEXT",
        "tecnico_asignado TEXT",
        "horas_estimadas REAL",
        "horas_reales REAL",
        "coste_estimado REAL",
        "coste_real REAL",
        "observaciones TEXT",
        "incidencia_id INTEGER",
        "cerrado_por TEXT",
        "fecha_cierre TEXT",
        "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "ordenes_trabajo", col)


def _seed_pisos_basico(conn):
    empresa_id = _empresa_id()
    config = conn.execute(
        "SELECT id FROM pisos_configuracion WHERE empresa_id = ? LIMIT 1",
        (empresa_id,),
    ).fetchone()
    if not config:
        conn.execute(
            """
            INSERT INTO pisos_configuracion
            (repaso_diario, cambio_toallas_cada_dias, cambio_sabanas_cada_dias,
             desinfeccion_bano_diaria, amenities_diarios, modo_lavanderia,
             lavanderia_propia_nombre, lavanderia_externa_principal, empresa_id)
            VALUES (1, 1, 3, 1, 1, 'mixta', 'Lavanderia propia', 'Lavanderia externa habitual', ?)
            """,
            (empresa_id,),
        )
    zonas = conn.execute(
        "SELECT COUNT(*) AS total FROM pisos_zonas WHERE empresa_id = ?",
        (empresa_id,),
    ).fetchone()
    if zonas and zonas["total"]:
        _seed_personal_pisos_desde_rrhh(conn, empresa_id)
        return
    for zona in ZONAS_BASE_PISOS:
        conn.execute(
            """
            INSERT INTO pisos_zonas
            (nombre, tipo, bloque, planta, frecuencia, prioridad, productos_previstos, instrucciones, appcc_critico, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (*zona, empresa_id),
        )
    _seed_personal_pisos_desde_rrhh(conn, empresa_id)


def _crear_zonas_base_pisos(conn, empresa_id):
    creadas = 0
    for zona in ZONAS_BASE_PISOS:
        nombre, tipo, bloque, planta, frecuencia, prioridad, productos, instrucciones = zona
        existe = conn.execute(
            """
            SELECT id
            FROM pisos_zonas
            WHERE empresa_id = ?
              AND LOWER(nombre) = LOWER(?)
              AND COALESCE(bloque, '') = ?
              AND COALESCE(planta, '') = ?
            LIMIT 1
            """,
            (empresa_id, nombre, bloque, planta),
        ).fetchone()
        if existe:
            continue
        conn.execute(
            """
            INSERT INTO pisos_zonas
            (nombre, tipo, bloque, planta, frecuencia, prioridad, productos_previstos, instrucciones, appcc_critico, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (nombre, tipo, bloque, planta, frecuencia, prioridad, productos, instrucciones, empresa_id),
        )
        creadas += 1
    return creadas


def _seed_productos_pisos_basico(conn):
    empresa_id = _empresa_id()
    existentes = conn.execute(
        "SELECT COUNT(*) AS total FROM pisos_productos WHERE empresa_id = ?",
        (empresa_id,),
    ).fetchone()
    if existentes and existentes["total"]:
        return
    productos = [
        ("Gel/champu amenities", "amenities", "ud", 120, 50, "Office pisos", "Carro/almacen pisos"),
        ("Papel higienico", "papel", "rollo", 80, 30, "Office pisos", "Almacen pisos"),
        ("Bolsas basura", "papel", "ud", 150, 50, "Office pisos", "Almacen pisos"),
        ("Limpiador suelos", "quimico", "l", 20, 5, "Proveedor limpieza", "Quimicos"),
        ("Desinfectante banos", "quimico", "l", 20, 5, "Proveedor limpieza", "Quimicos"),
        ("Desinfectante superficies", "quimico", "l", 15, 5, "Proveedor limpieza", "Quimicos"),
        ("Bayetas", "utensilio", "ud", 40, 15, "Office pisos", "Almacen pisos"),
        ("Guantes", "utensilio", "par", 50, 20, "Office pisos", "Almacen pisos"),
        ("Toallas reposicion", "textil", "ud", 30, 15, "Lavanderia", "Lenceria"),
    ]
    for nombre, categoria, unidad, stock, minimo, proveedor, ubicacion in productos:
        conn.execute(
            """
            INSERT INTO pisos_productos
            (nombre, categoria, unidad, stock_actual, stock_minimo, proveedor, ubicacion, activo, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (nombre, categoria, unidad, stock, minimo, proveedor, ubicacion, empresa_id),
        )


def _seed_lavanderias_basico(conn):
    empresa_id = _empresa_id()
    existentes = conn.execute(
        "SELECT COUNT(*) AS total FROM pisos_lavanderias WHERE empresa_id = ?",
        (empresa_id,),
    ).fetchone()
    if existentes and existentes["total"]:
        return
    base = [
        ("Lavanderia propia", "propia", "", "", "", "", "Detergente y desinfectante homologado", "Registrar lote, producto y responsable."),
        ("Lavanderia externa habitual", "externa", "", "", "", "", "", "Registrar albaran, salida y retorno."),
    ]
    for row in base:
        conn.execute(
            """
            INSERT INTO pisos_lavanderias
            (nombre, tipo, contacto, telefono, email, direccion, productos_lavado, instrucciones, activo, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (*row, empresa_id),
        )


def _seed_personal_pisos_desde_rrhh(conn, empresa_id):
    existentes = conn.execute(
        "SELECT COUNT(*) AS total FROM pisos_personal WHERE empresa_id = ?",
        (empresa_id,),
    ).fetchone()
    if existentes and existentes["total"]:
        return
    if not _table_exists(conn, "empleados"):
        return
    empleados = conn.execute(
        """
        SELECT e.id, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) AS nombre, e.telefono,
               COALESCE(d.nombre, '') AS departamento_nombre,
               COALESCE(sd.nombre, '') AS subdepartamento_nombre
        FROM empleados e
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
        WHERE e.empresa_id = ? AND COALESCE(e.activo, 1) = 1
        ORDER BY e.nombre, e.apellidos
        """,
        (empresa_id,),
    ).fetchall()
    claves = ("PISOS", "LIMPIEZA", "LAVANDERIA", "LENCERIA")
    for empleado in empleados:
        texto = f"{empleado['departamento_nombre']} {empleado['subdepartamento_nombre']}".upper()
        if not any(clave in texto for clave in claves):
            continue
        conn.execute(
            """
            INSERT INTO pisos_personal (empleado_id, nombre, rol, telefono, empresa_id)
            VALUES (?, ?, 'camarera', ?, ?)
            """,
            (empleado["id"], empleado["nombre"], empleado["telefono"], empresa_id),
        )


def _personal_pisos(conn, empresa_id, solo_activos=True):
    where = "empresa_id = ?"
    params = [empresa_id]
    if solo_activos:
        where += " AND COALESCE(activo, 1) = 1"
    return conn.execute(
        f"""
        SELECT *
        FROM pisos_personal
        WHERE {where}
        ORDER BY CASE rol WHEN 'gobernanta' THEN 1 WHEN 'supervisora' THEN 2 ELSE 3 END, nombre
        """,
        params,
    ).fetchall()


def _productos_pisos(conn, empresa_id, solo_activos=True):
    where = ["pp.empresa_id = ?"]
    params = [empresa_id]
    if solo_activos:
        where.append("COALESCE(pp.activo, 1) = 1")
    producto_join = ""
    producto_select = "NULL AS producto_global_nombre, NULL AS producto_global_codigo"
    if _table_exists(conn, "productos"):
        producto_join = "LEFT JOIN productos p ON pp.producto_id = p.id"
        producto_select = "p.nombre AS producto_global_nombre, p.codigo AS producto_global_codigo"
    return conn.execute(
        f"""
        SELECT pp.*, {producto_select}
        FROM pisos_productos pp
        {producto_join}
        WHERE {" AND ".join(where)}
        ORDER BY COALESCE(pp.activo, 1) DESC, pp.categoria, pp.nombre
        """,
        params,
    ).fetchall()


def _lavanderias_pisos(conn, empresa_id, solo_activas=True, tipo=""):
    filtros = ["empresa_id = ?"]
    params = [empresa_id]
    if solo_activas:
        filtros.append("COALESCE(activo, 1) = 1")
    if tipo:
        filtros.append("tipo = ?")
        params.append(tipo)
    return conn.execute(
        f"""
        SELECT *
        FROM pisos_lavanderias
        WHERE {" AND ".join(filtros)}
        ORDER BY COALESCE(activo, 1) DESC,
                 CASE tipo WHEN 'propia' THEN 1 ELSE 2 END,
                 nombre
        """,
        params,
    ).fetchall()


def _circuito_lavanderia_default(conn, empresa_id):
    config = _config_pisos(conn, empresa_id)
    modo = config["modo_lavanderia"] if "modo_lavanderia" in config.keys() else "mixta"
    if modo == "externa":
        return "externa"
    return "propia"


def _productos_globales_limpieza(conn, empresa_id):
    if not _table_exists(conn, "productos"):
        return []
    return conn.execute(
        """
        SELECT id, nombre, codigo, unidad, cantidad, stock_minimo, familia
        FROM productos
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
        ORDER BY familia, nombre
        LIMIT 400
        """,
        (empresa_id,),
    ).fetchall()


def _productos_bajo_minimo_pisos(conn, empresa_id):
    return conn.execute(
        """
        SELECT *
        FROM pisos_productos
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND COALESCE(stock_actual, 0) <= COALESCE(stock_minimo, 0)
        ORDER BY categoria, nombre
        """,
        (empresa_id,),
    ).fetchall()


def _registrar_consumos_trabajo(conn, trabajo, form, empresa_id):
    producto_ids = form.getlist("producto_pisos_id")
    cantidades = form.getlist("cantidad_consumo")
    observaciones = form.get("observaciones_consumo") or ""
    registros = []
    for idx, producto_id_txt in enumerate(producto_ids):
        if not str(producto_id_txt or "").isdigit():
            continue
        cantidad = _to_float(cantidades[idx] if idx < len(cantidades) else 0, 0.0)
        if cantidad <= 0:
            continue
        producto = conn.execute(
            "SELECT * FROM pisos_productos WHERE id = ? AND empresa_id = ?",
            (int(producto_id_txt), empresa_id),
        ).fetchone()
        if not producto:
            continue
        cursor = conn.execute(
            """
            INSERT INTO pisos_consumos
            (trabajo_id, fecha, producto_pisos_id, producto_id, producto_nombre,
             categoria, cantidad, unidad, habitacion_id, zona_id, observaciones, usuario, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trabajo["id"],
                trabajo["fecha"],
                producto["id"],
                producto["producto_id"],
                producto["nombre"],
                producto["categoria"],
                cantidad,
                producto["unidad"],
                trabajo["habitacion_id"],
                trabajo["zona_id"],
                observaciones,
                _usuario_nombre(),
                empresa_id,
            ),
        )
        conn.execute(
            """
            UPDATE pisos_productos
            SET stock_actual = COALESCE(stock_actual, 0) - ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (cantidad, producto["id"], empresa_id),
        )
        registros.append(f"{producto['nombre']} {cantidad:g} {producto['unidad'] or ''}".strip())
        _registrar_traza(
            conn,
            "consumo",
            cursor.lastrowid,
            "registrar_consumo",
            "",
            f"-{cantidad:g}",
            f"Trabajo #{trabajo['id']}. {producto['nombre']}",
            empresa_id,
        )
    return registros


def _panel_pisos(conn, empresa_id, fecha_txt):
    trabajos_estado = conn.execute(
        """
        SELECT estado, COUNT(*) AS total
        FROM pisos_trabajos
        WHERE empresa_id = ? AND fecha = ?
        GROUP BY estado
        """,
        (empresa_id, fecha_txt),
    ).fetchall()
    habitaciones_estado = conn.execute(
        """
        SELECT COALESCE(estado, 'disponible') AS estado, COUNT(*) AS total
        FROM habitaciones_recepcion
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        GROUP BY COALESCE(estado, 'disponible')
        """,
        (empresa_id,),
    ).fetchall()
    productividad = conn.execute(
        """
        SELECT COALESCE(asignado_a, 'Sin asignar') AS asignado_a,
               COUNT(*) AS total,
               SUM(CASE WHEN estado = 'hecha' THEN 1 ELSE 0 END) AS hechas,
               SUM(CASE WHEN estado = 'incidencia' THEN 1 ELSE 0 END) AS incidencias
        FROM pisos_trabajos
        WHERE empresa_id = ? AND fecha = ?
        GROUP BY COALESCE(asignado_a, 'Sin asignar')
        ORDER BY asignado_a
        """,
        (empresa_id, fecha_txt),
    ).fetchall()
    lenceria = conn.execute(
        """
        SELECT SUM(COALESCE(sabanas, 0)) AS sabanas,
               SUM(COALESCE(toallas, 0)) AS toallas,
               SUM(COALESCE(alfombrines, 0)) AS alfombrines
        FROM pisos_lenceria_movimientos
        WHERE empresa_id = ? AND fecha = ?
        """,
        (empresa_id, fecha_txt),
    ).fetchone()
    incidencias_abiertas = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM incidencias
        WHERE empresa_id = ?
          AND COALESCE(estado, 'abierta') NOT IN ('cerrada', 'resuelta')
          AND COALESCE(tipo, '') LIKE 'pisos%'
        """,
        (empresa_id,),
    ).fetchone()
    objetos_guardados = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM pisos_objetos_perdidos
        WHERE empresa_id = ? AND COALESCE(estado, 'guardado') IN ('guardado', 'reclamado')
        """,
        (empresa_id,),
    ).fetchone()
    bajo_minimo = _productos_bajo_minimo_pisos(conn, empresa_id)
    pedidos_pendientes = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM pisos_pedidos_internos
        WHERE empresa_id = ? AND COALESCE(estado, 'pendiente') NOT IN ('cerrado', 'cancelado')
        """,
        (empresa_id,),
    ).fetchone()
    return {
        "trabajos_estado": {r["estado"]: r["total"] for r in trabajos_estado},
        "habitaciones_estado": {r["estado"]: r["total"] for r in habitaciones_estado},
        "productividad": productividad,
        "lenceria": {
            "sabanas": (lenceria["sabanas"] if lenceria else 0) or 0,
            "toallas": (lenceria["toallas"] if lenceria else 0) or 0,
            "alfombrines": (lenceria["alfombrines"] if lenceria else 0) or 0,
        },
        "incidencias_abiertas": (incidencias_abiertas["total"] if incidencias_abiertas else 0) or 0,
        "objetos_guardados": (objetos_guardados["total"] if objetos_guardados else 0) or 0,
        "productos_bajo_minimo": bajo_minimo,
        "pedidos_pendientes": (pedidos_pendientes["total"] if pedidos_pendientes else 0) or 0,
    }


def _render_pisos_registro_print(titulo, filtros, columnas, filas, resumen=None):
    return render_template(
        "pisos/registro_print.html",
        titulo=titulo,
        filtros=filtros,
        columnas=columnas,
        filas=filas,
        resumen=resumen or [],
        generado_en=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


def _config_pisos(conn, empresa_id):
    config = conn.execute(
        "SELECT * FROM pisos_configuracion WHERE empresa_id = ? LIMIT 1",
        (empresa_id,),
    ).fetchone()
    if config:
        return config
    conn.execute(
        """
        INSERT INTO pisos_configuracion
        (repaso_diario, cambio_toallas_cada_dias, cambio_sabanas_cada_dias,
         desinfeccion_bano_diaria, amenities_diarios, modo_lavanderia,
         lavanderia_propia_nombre, lavanderia_externa_principal, empresa_id)
        VALUES (1, 1, 3, 1, 1, 'mixta', 'Lavanderia propia', 'Lavanderia externa habitual', ?)
        """,
        (empresa_id,),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM pisos_configuracion WHERE empresa_id = ? LIMIT 1",
        (empresa_id,),
    ).fetchone()


def _usuario_nombre():
    if not has_request_context():
        return "sistema"
    return session.get("user_nombre") or session.get("user_email") or "pisos"


def _usuario_id():
    if not has_request_context():
        return None
    return session.get("user_id")


def _nombre_trabajador_sesion(conn=None, empresa_id=None):
    empleado_id = session.get("user_empleado_id") if has_request_context() else None
    if conn and empresa_id and empleado_id:
        row = conn.execute(
            "SELECT nombre FROM pisos_personal WHERE empleado_id = ? AND empresa_id = ? LIMIT 1",
            (empleado_id, empresa_id),
        ).fetchone()
        if row and row["nombre"]:
            return row["nombre"]
    return session.get("user_nombre") or session.get("user_email") or ""


def _trabajo_asignado_a_usuario(trabajo, conn, empresa_id):
    if _puede_pisos_gestionar():
        return True
    empleado_id = session.get("user_empleado_id")
    if empleado_id and "asignado_empleado_id" in trabajo.keys() and trabajo["asignado_empleado_id"] == empleado_id:
        return True
    asignado = _normalizar_rol(trabajo["asignado_a"] if "asignado_a" in trabajo.keys() else "")
    usuario = _normalizar_rol(_nombre_trabajador_sesion(conn, empresa_id))
    if asignado and usuario and asignado == usuario:
        return True
    return not asignado


def _registrar_traza(conn, entidad, entidad_id, accion, estado_anterior="", estado_nuevo="", detalle="", empresa_id=None):
    try:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
        user_agent = (request.headers.get("User-Agent") or "")[:250]
    except RuntimeError:
        ip = ""
        user_agent = ""
    conn.execute(
        """
        INSERT INTO pisos_trazabilidad
        (entidad, entidad_id, accion, estado_anterior, estado_nuevo, detalle,
         usuario_id, usuario_nombre, ip, user_agent, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entidad,
            entidad_id,
            accion,
            estado_anterior,
            estado_nuevo,
            detalle,
            _usuario_id(),
            _usuario_nombre(),
            ip,
            user_agent,
            empresa_id or _empresa_id(),
        ),
    )


def _appcc_plan_pisos(conn, empresa_id, tipo):
    tarea = f"Pisos - {TIPOS_PARTE.get(tipo, tipo)}"
    plan = conn.execute(
        """
        SELECT id
        FROM plan_limpieza
        WHERE empresa_id = ?
          AND area = 'Habitaciones'
          AND tarea = ?
        LIMIT 1
        """,
        (empresa_id, tarea),
    ).fetchone()
    if plan:
        return plan["id"]
    cursor = conn.execute(
        """
        INSERT INTO plan_limpieza
        (area, tarea, frecuencia, responsable, productos, instrucciones, empresa_id)
        VALUES ('Habitaciones', ?, 'diaria', 'Pisos', '', 'Registro generado desde el parte de pisos.', ?)
        """,
        (tarea, empresa_id),
    )
    plan_id = cursor.lastrowid
    _registrar_traza(
        conn,
        "appcc_plan",
        plan_id,
        "crear_plan_appcc",
        "",
        "activo",
        f"Plan APPCC creado para {tarea}",
        empresa_id,
    )
    return plan_id


def _registrar_appcc_pisos(conn, parte, productos_usados, resultado, empresa_id):
    if parte["appcc_registro_id"]:
        return parte["appcc_registro_id"]
    plan_id = _appcc_plan_pisos(conn, empresa_id, parte["tipo"])
    observaciones = (
        f"Habitacion {_habitacion_label(parte)}. Parte #{parte['id']}. "
        f"Tipo: {TIPOS_PARTE.get(parte['tipo'], parte['tipo'])}."
    )
    if productos_usados:
        observaciones += f" Productos: {productos_usados}."
    if resultado:
        observaciones += f" Resultado: {resultado}."
    cursor = conn.execute(
        """
        INSERT INTO registros_limpieza (plan_id, fecha, realizada_por, observaciones, empresa_id)
        VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?)
        """,
        (plan_id, _usuario_nombre(), observaciones, empresa_id),
    )
    registro_id = cursor.lastrowid
    _registrar_traza(
        conn,
        "parte",
        parte["id"],
        "registrar_appcc",
        "",
        str(registro_id),
        observaciones,
        empresa_id,
    )
    return registro_id


def _destino_trabajo(row):
    if not row:
        return "Sin destino"
    if "target_tipo" in row.keys() and row["target_tipo"] == "zona":
        return row["zona_nombre"] if "zona_nombre" in row.keys() else "Zona comun"
    numero = row["habitacion_numero"] if "habitacion_numero" in row.keys() else None
    nombre = row["habitacion_nombre"] if "habitacion_nombre" in row.keys() else None
    return numero or nombre or "Sin habitacion"


def _departamento_pisos_id(conn, empresa_id):
    for nombre in ("pisos", "housekeeping", "habitaciones"):
        row = conn.execute(
            """
            SELECT id
            FROM departamentos
            WHERE empresa_id = ?
              AND COALESCE(activo, 1) = 1
              AND lower(nombre) = ?
            LIMIT 1
            """,
            (empresa_id, nombre),
        ).fetchone()
        if row:
            return row["id"]
    cursor = conn.execute(
        """
        INSERT INTO departamentos (nombre, descripcion, activo, es_base, empresa_id)
        VALUES ('Pisos', 'Departamento de pisos, habitaciones y zonas comunes', 1, 1, ?)
        """,
        (empresa_id,),
    )
    return cursor.lastrowid


def _trabajo_requiere_registro_limpieza(trabajo):
    if not trabajo:
        return False
    tarea_codigo = trabajo["tarea_codigo"] if "tarea_codigo" in trabajo.keys() else ""
    if tarea_codigo in TAREAS_REGISTRO_LIMPIEZA_PISOS:
        if trabajo["target_tipo"] == "zona" and "zona_appcc_critico" in trabajo.keys():
            return int(trabajo["zona_appcc_critico"] or 0) == 1
        return True
    return False


def _contexto_operativo_trabajo(conn, trabajo, empresa_id):
    departamento_id = None
    subdepartamento_id = None
    punto_venta_id = None
    if trabajo and "zona_departamento_id" in trabajo.keys():
        departamento_id = trabajo["zona_departamento_id"]
        subdepartamento_id = trabajo["zona_subdepartamento_id"]
        punto_venta_id = trabajo["zona_punto_venta_id"]
    departamento_id = departamento_id or _departamento_pisos_id(conn, empresa_id)
    ubicacion = _destino_trabajo(trabajo)
    return {
        "departamento_id": departamento_id,
        "subdepartamento_id": subdepartamento_id,
        "punto_venta_id": punto_venta_id,
        "ubicacion": ubicacion,
        "habitacion_id": trabajo["habitacion_id"] if trabajo and "habitacion_id" in trabajo.keys() else None,
        "zona_id": trabajo["zona_id"] if trabajo and "zona_id" in trabajo.keys() else None,
    }


def _contexto_operativo_parte(conn, parte, empresa_id):
    return {
        "departamento_id": _departamento_pisos_id(conn, empresa_id),
        "subdepartamento_id": None,
        "punto_venta_id": None,
        "ubicacion": _habitacion_label(parte),
        "habitacion_id": parte["habitacion_id"] if parte and "habitacion_id" in parte.keys() else None,
        "zona_id": None,
    }


def _codigo_equipo_pisos(contexto):
    if contexto.get("habitacion_id"):
        return f"PISOS-HAB-{contexto['habitacion_id']}"
    if contexto.get("zona_id"):
        return f"PISOS-ZONA-{contexto['zona_id']}"
    ubicacion = _normalizar_rol(contexto.get("ubicacion") or "general").replace(" ", "-")[:40]
    return f"PISOS-{ubicacion or 'GENERAL'}"


def _equipo_mantenimiento_pisos_contexto(conn, empresa_id, contexto, detalle=""):
    ensure_mantenimiento_context_schema(conn)
    codigo = _codigo_equipo_pisos(contexto)
    equipo = conn.execute(
        """
        SELECT id
        FROM equipos_mantenimiento
        WHERE empresa_id = ?
          AND COALESCE(codigo, '') = ?
        LIMIT 1
        """,
        (empresa_id, codigo),
    ).fetchone()
    if equipo:
        return equipo["id"]
    ubicacion = contexto.get("ubicacion") or "Pisos"
    nombre = f"{ubicacion} - inventario general"
    tipo = "habitacion" if contexto.get("habitacion_id") else ("zona" if contexto.get("zona_id") else "instalacion")
    cursor = conn.execute(
        """
        INSERT INTO equipos_mantenimiento
        (codigo, nombre, tipo, familia, categoria_operativa, clase_activo, criticidad,
         inventariable, requiere_appcc, ubicacion, departamento_id, subdepartamento_id,
         punto_venta_id, origen_modulo, habitacion_id, zona_id, activo, empresa_id)
        VALUES (?, ?, ?, 'pisos', 'mantenimiento', 'ubicacion', 'media',
                1, 0, ?, ?, ?, ?, 'pisos', ?, ?, 1, ?)
        """,
        (
            codigo,
            nombre,
            tipo,
            ubicacion,
            contexto.get("departamento_id"),
            contexto.get("subdepartamento_id"),
            contexto.get("punto_venta_id"),
            contexto.get("habitacion_id"),
            contexto.get("zona_id"),
            empresa_id,
        ),
    )
    equipo_id = cursor.lastrowid
    _registrar_traza(conn, "mantenimiento_equipo", equipo_id, "crear_equipo", "", "activo", detalle or nombre, empresa_id)
    return equipo_id


def _trabajo_existente(conn, empresa_id, fecha_txt, target_tipo, tarea_codigo, habitacion_id=None, zona_id=None, reserva_id=None):
    return conn.execute(
        """
        SELECT id
        FROM pisos_trabajos
        WHERE empresa_id = ?
          AND fecha = ?
          AND target_tipo = ?
          AND tarea_codigo = ?
          AND COALESCE(habitacion_id, 0) = COALESCE(?, 0)
          AND COALESCE(zona_id, 0) = COALESCE(?, 0)
          AND COALESCE(reserva_id, 0) = COALESCE(?, 0)
        LIMIT 1
        """,
        (empresa_id, fecha_txt, target_tipo, tarea_codigo, habitacion_id, zona_id, reserva_id),
    ).fetchone()


def _crear_trabajo_si_falta(
    conn,
    empresa_id,
    fecha_txt,
    tarea_codigo,
    target_tipo="habitacion",
    habitacion_id=None,
    zona_id=None,
    reserva_id=None,
    bloque="",
    planta="",
    origen="regla",
    prioridad=None,
    productos_previstos="",
    checklist="",
    observaciones="",
    sabanas_previstas=0,
    toallas_previstas=0,
    alfombrines_previstos=0,
):
    if _trabajo_existente(conn, empresa_id, fecha_txt, target_tipo, tarea_codigo, habitacion_id, zona_id, reserva_id):
        return False
    tarea = TAREAS_PISOS.get(tarea_codigo, {"nombre": tarea_codigo, "categoria": "habitacion", "prioridad": "media"})
    prioridad_final = prioridad or tarea.get("prioridad") or "media"
    cursor = conn.execute(
        """
        INSERT INTO pisos_trabajos
        (fecha, origen, target_tipo, habitacion_id, zona_id, reserva_id,
         tarea_codigo, tarea_nombre, categoria, estado, prioridad, bloque, planta,
         productos_previstos, checklist, observaciones, sabanas_previstas,
         toallas_previstas, alfombrines_previstos, creado_por, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fecha_txt,
            origen,
            target_tipo,
            habitacion_id,
            zona_id,
            reserva_id,
            tarea_codigo,
            tarea["nombre"],
            tarea["categoria"],
            prioridad_final,
            bloque,
            planta,
            productos_previstos,
            checklist,
            observaciones,
            int(sabanas_previstas or 0),
            int(toallas_previstas or 0),
            int(alfombrines_previstos or 0),
            _usuario_id(),
            empresa_id,
        ),
    )
    trabajo_id = cursor.lastrowid
    _registrar_traza(
        conn,
        "trabajo",
        trabajo_id,
        "crear_trabajo",
        "",
        "pendiente",
        f"{fecha_txt}. {target_tipo}. {tarea['nombre']}. {observaciones}".strip(),
        empresa_id,
    )
    return trabajo_id


def _dias_desde_entrada(reserva, fecha_obj):
    entrada = _parse_date(reserva["fecha_entrada"])
    if not entrada or not fecha_obj:
        return 0
    return max((fecha_obj - entrada).days, 0)


def _frecuencia_zona_activa(frecuencia, fecha_obj):
    frecuencia = (frecuencia or "diaria").strip().lower()
    if frecuencia == "diaria":
        return True
    dias = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    if frecuencia in dias:
        return fecha_obj.weekday() == dias.index(frecuencia)
    if frecuencia == "semanal":
        return fecha_obj.weekday() == 0
    return True


def _generar_trabajos_dia(conn, empresa_id, fecha_txt):
    creados = 0
    config = _config_pisos(conn, empresa_id)
    fecha_obj = _parse_date(fecha_txt, date.today())
    reservas = _reservas_dia(conn, empresa_id, fecha_txt)
    for reserva in reservas:
        habitacion_id = reserva["habitacion_id"]
        bloque = reserva["habitacion_bloque"] if "habitacion_bloque" in reserva.keys() else ""
        planta = reserva["habitacion_planta"] if "habitacion_planta" in reserva.keys() else ""
        personas = (reserva["adultos"] or 0) + (reserva["menores"] or 0)
        avisos_habitacion = _avisos_habitacion_reserva(reserva)
        if reserva["fecha_salida"] and str(reserva["fecha_salida"])[:10] == fecha_txt:
            obs = f"Salida {reserva['codigo'] or reserva['id']} - {reserva['titular_nombre'] or ''}".strip()
            if avisos_habitacion:
                obs = f"{obs}. {avisos_habitacion}"
            checklist = "Retirar ropa usada; limpiar habitacion; desinfectar bano; revisar minibar/amenities; ventilar; revisar incidencias"
            if _crear_trabajo_si_falta(
                conn, empresa_id, fecha_txt, "salida", "habitacion", habitacion_id, None, reserva["id"],
                bloque, planta, "reserva", "alta", "", checklist, obs,
                sabanas_previstas=max(personas, 1), toallas_previstas=max(personas, 1), alfombrines_previstos=1,
            ):
                creados += 1
        if reserva["fecha_entrada"] and str(reserva["fecha_entrada"])[:10] == fecha_txt:
            obs = f"Entrada {reserva['codigo'] or reserva['id']} - {reserva['titular_nombre'] or ''}".strip()
            if avisos_habitacion:
                obs = f"{obs}. {avisos_habitacion}"
            checklist = "Preparar habitacion; comprobar bano; colocar toallas; revisar amenities; revisar camas/supletoria/cuna; confirmar estado para recepcion"
            if _crear_trabajo_si_falta(
                conn, empresa_id, fecha_txt, "entrada", "habitacion", habitacion_id, None, reserva["id"],
                bloque, planta, "reserva", "alta", "", checklist, obs,
                toallas_previstas=max(personas, 1), alfombrines_previstos=1,
            ):
                creados += 1
        entrada = _parse_date(reserva["fecha_entrada"])
        salida = _parse_date(reserva["fecha_salida"])
        if entrada and salida and fecha_obj and entrada < fecha_obj < salida:
            dias = _dias_desde_entrada(reserva, fecha_obj)
            obs = f"Estancia dia {dias} - {reserva['titular_nombre'] or reserva['codigo'] or reserva['id']}".strip()
            if avisos_habitacion:
                obs = f"{obs}. {avisos_habitacion}"
            if config["repaso_diario"]:
                if _crear_trabajo_si_falta(
                    conn, empresa_id, fecha_txt, "repaso", "habitacion", habitacion_id, None, reserva["id"],
                    bloque, planta, "regla", "media", "", "Hacer cama; papeleras; suelo; reposicion basica; revisar avisos", obs,
                ):
                    creados += 1
            if config["desinfeccion_bano_diaria"]:
                if _crear_trabajo_si_falta(
                    conn, empresa_id, fecha_txt, "bano_desinfeccion", "habitacion", habitacion_id, None, reserva["id"],
                    bloque, planta, "regla", "alta", "Desinfectante banos", "Lavabo; ducha/banera; inodoro; pomos; suelo; reposicion papel", obs,
                ):
                    creados += 1
            cambio_toallas = int(config["cambio_toallas_cada_dias"] or 0)
            if cambio_toallas and dias > 0 and dias % cambio_toallas == 0:
                if _crear_trabajo_si_falta(
                    conn, empresa_id, fecha_txt, "toallas", "habitacion", habitacion_id, None, reserva["id"],
                    bloque, planta, "regla", "media", "", "Retirar toallas usadas; entregar toallas limpias; anotar lote si aplica", obs,
                    toallas_previstas=max(personas, 1), alfombrines_previstos=1,
                ):
                    creados += 1
            cambio_sabanas = int(config["cambio_sabanas_cada_dias"] or 0)
            if cambio_sabanas and dias > 0 and dias % cambio_sabanas == 0:
                if _crear_trabajo_si_falta(
                    conn, empresa_id, fecha_txt, "sabanas", "habitacion", habitacion_id, None, reserva["id"],
                    bloque, planta, "regla", "media", "", "Retirar sabanas usadas; colocar sabanas limpias; anotar lote si aplica", obs,
                    sabanas_previstas=max(personas, 1),
                ):
                    creados += 1
            if config["amenities_diarios"]:
                if _crear_trabajo_si_falta(
                    conn, empresa_id, fecha_txt, "amenities", "habitacion", habitacion_id, None, reserva["id"],
                    bloque, planta, "regla", "baja", "", "Reposicion de amenities segun estandar del establecimiento", obs,
                ):
                    creados += 1

    habitaciones_estado = conn.execute(
        """
        SELECT id, numero, estado, bloque, planta
        FROM habitaciones_recepcion
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND COALESCE(estado, 'disponible') IN ('limpieza', 'repaso', 'mantenimiento')
        """,
        (empresa_id,),
    ).fetchall()
    for habitacion in habitaciones_estado:
        estado = habitacion["estado"] or "repaso"
        codigo = "mantenimiento" if estado == "mantenimiento" else ("salida" if estado == "limpieza" else "repaso")
        obs = f"Estado habitacion: {estado}"
        if _crear_trabajo_si_falta(
            conn,
            empresa_id,
            fecha_txt,
            codigo,
            "habitacion",
            habitacion["id"],
            None,
            None,
            habitacion["bloque"] or "",
            habitacion["planta"] or "",
            "manual",
            "alta" if codigo != "repaso" else "media",
            "",
            "",
            obs,
        ):
            creados += 1

    zonas = conn.execute(
        """
        SELECT *
        FROM pisos_zonas
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY bloque, planta, nombre
        """,
        (empresa_id,),
    ).fetchall()
    for zona in zonas:
        if not _frecuencia_zona_activa(zona["frecuencia"], fecha_obj):
            continue
        nombre = TIPOS_ZONA.get(zona["tipo"], "Zona comun")
        checklist = zona["instrucciones"] or f"Limpieza documentada de {nombre.lower()}."
        if _crear_trabajo_si_falta(
            conn,
            empresa_id,
            fecha_txt,
            "zona_comun",
            "zona",
            None,
            zona["id"],
            None,
            zona["bloque"] or "",
            zona["planta"] or "",
            "zona",
            zona["prioridad"] or "media",
            zona["productos_previstos"] or "",
            checklist,
            zona["nombre"],
        ):
            creados += 1
    return creados


def _avisos_habitacion_reserva(reserva):
    if not reserva:
        return ""
    avisos = []
    pref = reserva["preferencia_cama"] if "preferencia_cama" in reserva.keys() else ""
    if pref:
        avisos.append(f"preferencia cama: {TIPOS_CAMA.get(pref, pref)}")
    if "supletoria_solicitada" in reserva.keys() and reserva["supletoria_solicitada"]:
        avisos.append("cama supletoria solicitada")
    if "cuna_solicitada" in reserva.keys() and reserva["cuna_solicitada"]:
        avisos.append("cuna solicitada")
    obs = reserva["observaciones_habitacion"] if "observaciones_habitacion" in reserva.keys() else ""
    if obs:
        avisos.append(obs)
    return "; ".join(avisos)


def _habitacion_label(row):
    if not row:
        return "Sin habitacion"
    numero = row["habitacion_numero"] if "habitacion_numero" in row.keys() else row["numero"]
    nombre = row["habitacion_nombre"] if "habitacion_nombre" in row.keys() else row["nombre"]
    return numero or nombre or "Sin habitacion"


def _parte_existente(conn, empresa_id, fecha_txt, habitacion_id, tipo, reserva_id=None):
    return conn.execute(
        """
        SELECT id
        FROM pisos_partes_limpieza
        WHERE empresa_id = ?
          AND fecha = ?
          AND habitacion_id = ?
          AND tipo = ?
          AND COALESCE(reserva_id, 0) = COALESCE(?, 0)
        LIMIT 1
        """,
        (empresa_id, fecha_txt, habitacion_id, tipo, reserva_id),
    ).fetchone()


def _crear_parte_si_falta(conn, empresa_id, fecha_txt, habitacion_id, tipo, reserva_id=None, prioridad="media", observaciones=""):
    if not habitacion_id or _parte_existente(conn, empresa_id, fecha_txt, habitacion_id, tipo, reserva_id):
        return False
    cursor = conn.execute(
        """
        INSERT INTO pisos_partes_limpieza
        (fecha, habitacion_id, reserva_id, tipo, estado, prioridad, observaciones, creado_por, empresa_id)
        VALUES (?, ?, ?, ?, 'pendiente', ?, ?, ?, ?)
        """,
        (fecha_txt, habitacion_id, reserva_id, tipo, prioridad, observaciones, _usuario_id(), empresa_id),
    )
    parte_id = cursor.lastrowid
    _registrar_traza(
        conn,
        "parte",
        parte_id,
        "crear_parte",
        "",
        "pendiente",
        f"Fecha {fecha_txt}. Habitacion #{habitacion_id}. Tipo {tipo}. {observaciones}".strip(),
        empresa_id,
    )
    return parte_id


def _reservas_dia(conn, empresa_id, fecha_txt):
    return conn.execute(
        """
        SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               h.bloque AS habitacion_bloque, h.planta AS habitacion_planta
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE r.empresa_id = ?
          AND r.habitacion_id IS NOT NULL
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'no_show')
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) >= date(?)
        ORDER BY h.bloque, h.planta, h.numero, r.titular_nombre
        """,
        (empresa_id, fecha_txt, fecha_txt),
    ).fetchall()


def _generar_partes_dia(conn, empresa_id, fecha_txt):
    return _generar_trabajos_dia(conn, empresa_id, fecha_txt)
    creados = 0
    reservas = _reservas_dia(conn, empresa_id, fecha_txt)
    for reserva in reservas:
        habitacion_id = reserva["habitacion_id"]
        if reserva["fecha_salida"] and str(reserva["fecha_salida"])[:10] == fecha_txt:
            obs = f"Salida {reserva['codigo'] or reserva['id']} - {reserva['titular_nombre'] or ''}".strip()
            if _crear_parte_si_falta(conn, empresa_id, fecha_txt, habitacion_id, "salida", reserva["id"], "alta", obs):
                creados += 1
        if reserva["fecha_entrada"] and str(reserva["fecha_entrada"])[:10] == fecha_txt:
            obs = f"Entrada {reserva['codigo'] or reserva['id']} - {reserva['titular_nombre'] or ''}".strip()
            if _crear_parte_si_falta(conn, empresa_id, fecha_txt, habitacion_id, "entrada", reserva["id"], "alta", obs):
                creados += 1
        entrada = _parse_date(reserva["fecha_entrada"])
        salida = _parse_date(reserva["fecha_salida"])
        fecha_obj = _parse_date(fecha_txt)
        if entrada and salida and fecha_obj and entrada < fecha_obj < salida:
            obs = f"Estancia {reserva['codigo'] or reserva['id']} - {reserva['titular_nombre'] or ''}".strip()
            if _crear_parte_si_falta(conn, empresa_id, fecha_txt, habitacion_id, "estancia", reserva["id"], "media", obs):
                creados += 1

    habitaciones_estado = conn.execute(
        """
        SELECT id, numero, estado
        FROM habitaciones_recepcion
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND COALESCE(estado, 'disponible') IN ('limpieza', 'repaso', 'mantenimiento')
        """,
        (empresa_id,),
    ).fetchall()
    for habitacion in habitaciones_estado:
        estado = habitacion["estado"] or "repaso"
        tipo = "mantenimiento" if estado == "mantenimiento" else ("salida" if estado == "limpieza" else "repaso")
        prioridad = "alta" if tipo in {"salida", "mantenimiento"} else "media"
        obs = f"Estado habitacion: {estado}"
        if _crear_parte_si_falta(conn, empresa_id, fecha_txt, habitacion["id"], tipo, None, prioridad, obs):
            creados += 1
    return creados


def _partes_query(conn, empresa_id, fecha_txt, estado="", bloque="", planta="", trabajador=""):
    filtros = ["p.empresa_id = ?", "p.fecha = ?"]
    params = [empresa_id, fecha_txt]
    if estado:
        filtros.append("p.estado = ?")
        params.append(estado)
    if bloque:
        filtros.append("COALESCE(p.bloque, h.bloque, z.bloque, '') = ?")
        params.append(bloque)
    if planta:
        filtros.append("COALESCE(p.planta, h.planta, z.planta, '') = ?")
        params.append(planta)
    if trabajador:
        filtros.append("COALESCE(p.asignado_a, '') = ?")
        params.append(trabajador)
    return conn.execute(
        f"""
        SELECT p.*, p.tarea_codigo AS tipo, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               z.nombre AS zona_nombre, z.tipo AS zona_tipo, z.appcc_critico AS zona_appcc_critico,
               z.departamento_id AS zona_departamento_id,
               z.subdepartamento_id AS zona_subdepartamento_id,
               z.punto_venta_id AS zona_punto_venta_id,
               COALESCE(p.bloque, h.bloque, z.bloque) AS bloque,
               COALESCE(p.planta, h.planta, z.planta) AS planta,
               h.estado AS habitacion_estado,
               eq.tipo_cama, eq.camas_matrimonio, eq.camas_individuales, eq.camas_juntas,
               eq.sofa_cama, eq.supletoria_posible, eq.supletorias_max, eq.cuna_posible,
               eq.tv, eq.minibar, eq.aire_tipo, eq.armario, eq.mesas, eq.sillas,
               eq.mesillas, eq.tumbonas, eq.tendedero, eq.caja_fuerte, eq.terraza,
               eq.inventario_extra, eq.observaciones AS equipamiento_observaciones,
               r.codigo AS reserva_codigo, r.titular_nombre, r.fecha_entrada, r.fecha_salida,
               r.adultos, r.menores, r.alergias_resumen, r.observaciones_operativas,
               r.preferencia_cama, r.supletoria_solicitada, r.cuna_solicitada, r.observaciones_habitacion
        FROM pisos_trabajos p
        LEFT JOIN habitaciones_recepcion h ON p.habitacion_id = h.id
        LEFT JOIN habitaciones_equipamiento eq ON h.id = eq.habitacion_id AND eq.empresa_id = p.empresa_id
        LEFT JOIN pisos_zonas z ON p.zona_id = z.id
        LEFT JOIN reservas_recepcion r ON p.reserva_id = r.id
        WHERE {" AND ".join(filtros)}
        ORDER BY CASE p.prioridad WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END,
                 COALESCE(p.bloque, h.bloque, z.bloque),
                 COALESCE(p.planta, h.planta, z.planta),
                 p.target_tipo, h.numero, z.nombre, p.tarea_codigo, p.id
        """,
        params,
    ).fetchall()


def _habitaciones_pisos(conn, empresa_id, fecha_txt, bloque="", planta=""):
    filtros = ["h.empresa_id = ?", "COALESCE(h.activo, 1) = 1"]
    params = [empresa_id]
    if bloque:
        filtros.append("COALESCE(h.bloque, '') = ?")
        params.append(bloque)
    if planta:
        filtros.append("COALESCE(h.planta, '') = ?")
        params.append(planta)
    habitaciones = conn.execute(
        f"""
        SELECT h.*, th.nombre AS tipo_nombre,
               eq.tipo_cama, eq.camas_matrimonio, eq.camas_individuales, eq.camas_juntas,
               eq.sofa_cama, eq.supletoria_posible, eq.supletorias_max, eq.cuna_posible,
               eq.tv, eq.minibar, eq.aire_tipo, eq.armario, eq.mesas, eq.sillas,
               eq.mesillas, eq.tumbonas, eq.tendedero, eq.caja_fuerte, eq.terraza,
               eq.inventario_extra, eq.observaciones AS equipamiento_observaciones,
               eq.revisado_at AS equipamiento_revisado_at
        FROM habitaciones_recepcion h
        LEFT JOIN recepcion_tipos_habitacion th ON h.tipo_habitacion_id = th.id
        LEFT JOIN habitaciones_equipamiento eq ON h.id = eq.habitacion_id AND eq.empresa_id = h.empresa_id
        WHERE {" AND ".join(filtros)}
        ORDER BY h.bloque, h.planta, h.numero, h.nombre
        """,
        params,
    ).fetchall()
    reservas = _reservas_dia(conn, empresa_id, fecha_txt)
    partes = _partes_query(conn, empresa_id, fecha_txt, bloque=bloque, planta=planta)
    por_habitacion = {}
    for habitacion in habitaciones:
        por_habitacion[habitacion["id"]] = {
            "habitacion": habitacion,
            "ocupacion": [],
            "entradas": [],
            "salidas": [],
            "partes": [],
        }
    for reserva in reservas:
        item = por_habitacion.get(reserva["habitacion_id"])
        if not item:
            continue
        if str(reserva["fecha_entrada"])[:10] == fecha_txt:
            item["entradas"].append(reserva)
        if str(reserva["fecha_salida"])[:10] == fecha_txt:
            item["salidas"].append(reserva)
        entrada = _parse_date(reserva["fecha_entrada"])
        salida = _parse_date(reserva["fecha_salida"])
        fecha_obj = _parse_date(fecha_txt)
        if entrada and salida and fecha_obj and entrada <= fecha_obj < salida:
            item["ocupacion"].append(reserva)
    for parte in partes:
        item = por_habitacion.get(parte["habitacion_id"])
        if item:
            item["partes"].append(parte)
    return list(por_habitacion.values())


def _opciones_filtro(conn, empresa_id):
    bloques = conn.execute(
        """
        SELECT DISTINCT COALESCE(bloque, '') AS valor
        FROM habitaciones_recepcion
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 AND COALESCE(bloque, '') <> ''
        ORDER BY bloque
        """,
        (empresa_id,),
    ).fetchall()
    plantas = conn.execute(
        """
        SELECT DISTINCT COALESCE(planta, '') AS valor
        FROM habitaciones_recepcion
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 AND COALESCE(planta, '') <> ''
        ORDER BY planta
        """,
        (empresa_id,),
    ).fetchall()
    return bloques, plantas


def _resumen_pisos(conn, empresa_id, fecha_txt):
    partes = conn.execute(
        """
        SELECT estado, COUNT(*) AS total
        FROM pisos_trabajos
        WHERE empresa_id = ? AND fecha = ?
        GROUP BY estado
        """,
        (empresa_id, fecha_txt),
    ).fetchall()
    categorias = conn.execute(
        """
        SELECT categoria, COUNT(*) AS total
        FROM pisos_trabajos
        WHERE empresa_id = ? AND fecha = ?
        GROUP BY categoria
        """,
        (empresa_id, fecha_txt),
    ).fetchall()
    lenceria = conn.execute(
        """
        SELECT SUM(COALESCE(sabanas_previstas, 0)) AS sabanas,
               SUM(COALESCE(toallas_previstas, 0)) AS toallas,
               SUM(COALESCE(alfombrines_previstos, 0)) AS alfombrines
        FROM pisos_trabajos
        WHERE empresa_id = ? AND fecha = ?
        """,
        (empresa_id, fecha_txt),
    ).fetchone()
    habitaciones = conn.execute(
        """
        SELECT COALESCE(estado, 'disponible') AS estado, COUNT(*) AS total
        FROM habitaciones_recepcion
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        GROUP BY COALESCE(estado, 'disponible')
        """,
        (empresa_id,),
    ).fetchall()
    reservas = conn.execute(
        """
        SELECT
          SUM(CASE WHEN date(fecha_entrada) = date(?) THEN 1 ELSE 0 END) AS entradas,
          SUM(CASE WHEN date(fecha_salida) = date(?) THEN 1 ELSE 0 END) AS salidas
        FROM reservas_recepcion
        WHERE empresa_id = ?
          AND habitacion_id IS NOT NULL
          AND COALESCE(estado, 'confirmada') NOT IN ('cancelada', 'no_show')
          AND (date(fecha_entrada) = date(?) OR date(fecha_salida) = date(?))
        """,
        (fecha_txt, fecha_txt, empresa_id, fecha_txt, fecha_txt),
    ).fetchone()
    return {
        "partes": {row["estado"]: row["total"] for row in partes},
        "categorias": {row["categoria"]: row["total"] for row in categorias},
        "habitaciones": {row["estado"]: row["total"] for row in habitaciones},
        "entradas": reservas["entradas"] or 0,
        "salidas": reservas["salidas"] or 0,
        "lenceria": {
            "sabanas": lenceria["sabanas"] or 0,
            "toallas": lenceria["toallas"] or 0,
            "alfombrines": lenceria["alfombrines"] or 0,
        },
    }


def _trazabilidad_query(conn, empresa_id, desde, hasta, entidad="", q=""):
    filtros = ["t.empresa_id = ?", "date(t.fecha) BETWEEN date(?) AND date(?)"]
    params = [empresa_id, desde, hasta]
    if entidad:
        filtros.append("t.entidad = ?")
        params.append(entidad)
    if q:
        like = f"%{q}%"
        filtros.append(
            """
            (COALESCE(t.detalle, '') LIKE ?
             OR COALESCE(t.usuario_nombre, '') LIKE ?
             OR COALESCE(hp.numero, ht.numero, hh.numero, '') LIKE ?
             OR COALESCE(hp.nombre, ht.nombre, hh.nombre, zt.nombre, '') LIKE ?)
            """
        )
        params.extend([like, like, like, like])
    return conn.execute(
        f"""
        SELECT t.*,
               p.fecha AS parte_fecha,
               p.tipo AS parte_tipo,
               p.estado AS parte_estado,
               tr.fecha AS trabajo_fecha,
               tr.tarea_nombre AS trabajo_tarea,
               tr.estado AS trabajo_estado,
               zt.nombre AS zona_nombre,
               COALESCE(hp.numero, ht.numero, hh.numero) AS habitacion_numero,
               COALESCE(hp.nombre, ht.nombre, hh.nombre) AS habitacion_nombre
        FROM pisos_trazabilidad t
        LEFT JOIN pisos_partes_limpieza p
          ON t.entidad = 'parte' AND t.entidad_id = p.id
        LEFT JOIN habitaciones_recepcion hp
          ON p.habitacion_id = hp.id
        LEFT JOIN pisos_trabajos tr
          ON t.entidad = 'trabajo' AND t.entidad_id = tr.id
        LEFT JOIN habitaciones_recepcion ht
          ON tr.habitacion_id = ht.id
        LEFT JOIN pisos_zonas zt
          ON tr.zona_id = zt.id
        LEFT JOIN habitaciones_recepcion hh
          ON t.entidad = 'habitacion' AND t.entidad_id = hh.id
        WHERE {" AND ".join(filtros)}
        ORDER BY datetime(t.fecha) DESC, t.id DESC
        LIMIT 500
        """,
        params,
    ).fetchall()


def _crear_incidencia_pisos(conn, parte, descripcion, empresa_id, tipo_incidencia="mantenimiento", gravedad="media"):
    if not descripcion:
        descripcion = "Incidencia comunicada desde pisos."
    tipo_incidencia = tipo_incidencia if tipo_incidencia in TIPOS_INCIDENCIA_PISOS else "mantenimiento"
    gravedad = gravedad if gravedad in GRAVEDADES_INCIDENCIA else "media"
    ensure_incidencias_context_schema(conn)
    contexto = _contexto_operativo_parte(conn, parte, empresa_id)
    destino_modulo = "mantenimiento" if tipo_incidencia in INCIDENCIAS_A_MANTENIMIENTO_PISOS else "pisos"
    cursor = conn.execute(
        """
        INSERT INTO incidencias
        (tipo, referencia_id, usuario, descripcion, gravedad, estado, observaciones,
         origen_modulo, destino_modulo, categoria_operativa, departamento_id,
         subdepartamento_id, punto_venta_id, ubicacion, habitacion_id, zona_id,
         requiere_mantenimiento, requiere_appcc, origen_tabla, origen_id, empresa_id)
        VALUES (?, ?, ?, ?, ?, 'abierta', ?, 'pisos', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'pisos_partes_limpieza', ?, ?)
        """,
        (
            f"pisos_{tipo_incidencia}",
            parte["id"],
            session.get("user_nombre") or session.get("user_email") or "pisos",
            descripcion,
            gravedad,
            f"Habitacion {_habitacion_label(parte)}",
            destino_modulo,
            tipo_incidencia,
            contexto["departamento_id"],
            contexto["subdepartamento_id"],
            contexto["punto_venta_id"],
            contexto["ubicacion"],
            contexto["habitacion_id"],
            contexto["zona_id"],
            1 if tipo_incidencia in INCIDENCIAS_A_MANTENIMIENTO_PISOS else 0,
            parte["id"],
            empresa_id,
        ),
    )
    incidencia_id = cursor.lastrowid
    _registrar_traza(
        conn,
        "parte",
        parte["id"],
        "crear_incidencia",
        "",
        str(incidencia_id),
        descripcion,
        empresa_id,
    )
    return incidencia_id


def _numero_ot_pisos(conn):
    fecha_codigo = datetime.now().strftime("%Y%m%d")
    fecha_txt = datetime.now().strftime("%Y-%m-%d")
    total = conn.execute(
        "SELECT COUNT(*) AS total FROM ordenes_trabajo WHERE fecha_apertura LIKE ?",
        (f"{fecha_txt}%",),
    ).fetchone()["total"] or 0
    for extra in range(1, 1000):
        numero = f"OT-{fecha_codigo}-{total + extra:03d}"
        existe = conn.execute(
            "SELECT id FROM ordenes_trabajo WHERE numero_orden = ? LIMIT 1",
            (numero,),
        ).fetchone()
        if not existe:
            return numero
    return f"OT-{fecha_codigo}-{int(datetime.now().timestamp())}"


def _equipo_mantenimiento_pisos(conn, empresa_id):
    nombre = "Pisos - habitaciones y zonas"
    equipo = conn.execute(
        """
        SELECT id
        FROM equipos_mantenimiento
        WHERE empresa_id = ? AND nombre = ?
        LIMIT 1
        """,
        (empresa_id, nombre),
    ).fetchone()
    if equipo:
        return equipo["id"]
    cursor = conn.execute(
        """
        INSERT INTO equipos_mantenimiento
        (nombre, tipo, ubicacion, activo, empresa_id)
        VALUES (?, 'instalacion', 'Habitaciones y zonas comunes', 1, ?)
        """,
        (nombre, empresa_id),
    )
    equipo_id = cursor.lastrowid
    _registrar_traza(conn, "mantenimiento_equipo", equipo_id, "crear_equipo", "", "activo", nombre, empresa_id)
    return equipo_id


def _prioridad_mantenimiento(prioridad):
    prioridad = (prioridad or "media").strip().lower()
    if prioridad in {"urgente", "alta", "media", "baja"}:
        return prioridad
    return "media"


def _crear_ot_mantenimiento_desde_trabajo(conn, trabajo, incidencia_id, descripcion, empresa_id):
    if trabajo["mantenimiento_orden_id"]:
        return trabajo["mantenimiento_orden_id"]
    contexto = _contexto_operativo_trabajo(conn, trabajo, empresa_id)
    equipo_id = _equipo_mantenimiento_pisos_contexto(conn, empresa_id, contexto, descripcion)
    numero_orden = _numero_ot_pisos(conn)
    destino = _destino_trabajo(trabajo)
    detalle = descripcion or "Aviso creado desde pisos."
    descripcion_ot = (
        f"Aviso desde pisos.\n"
        f"Destino: {destino}\n"
        f"Trabajo: {trabajo['tarea_nombre']}\n"
        f"Fecha parte: {trabajo['fecha']}\n"
        f"Detalle: {detalle}\n"
        f"Trabajo pisos #{trabajo['id']}"
    )
    if trabajo["reserva_id"]:
        descripcion_ot += f"\nReserva #{trabajo['reserva_id']}"
    cursor = conn.execute(
        """
        INSERT INTO ordenes_trabajo
        (numero_orden, equipo_id, tipo, prioridad, estado, descripcion,
         tecnico_asignado, incidencia_id, observaciones, origen_modulo, origen_tipo,
         origen_id, departamento_id, subdepartamento_id, punto_venta_id, ubicacion,
         habitacion_id, zona_id, requiere_appcc, empresa_id)
        VALUES (?, ?, 'correctivo', ?, 'abierta', ?, '', ?, ?, 'pisos', 'trabajo',
                ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            numero_orden,
            equipo_id,
            _prioridad_mantenimiento(trabajo["prioridad"]),
            descripcion_ot,
            incidencia_id,
            f"Origen: pisos. Trabajo #{trabajo['id']}.",
            trabajo["id"],
            contexto["departamento_id"],
            contexto["subdepartamento_id"],
            contexto["punto_venta_id"],
            contexto["ubicacion"],
            contexto["habitacion_id"],
            contexto["zona_id"],
            empresa_id,
        ),
    )
    orden_id = cursor.lastrowid
    conn.execute(
        """
        UPDATE incidencias
        SET orden_trabajo_id = ?, equipo_mantenimiento_id = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (orden_id, equipo_id, incidencia_id, empresa_id),
    )
    conn.execute(
        """
        UPDATE pisos_trabajos
        SET mantenimiento_orden_id = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (orden_id, trabajo["id"], empresa_id),
    )
    _registrar_traza(
        conn,
        "trabajo",
        trabajo["id"],
        "crear_ot_mantenimiento",
        "",
        numero_orden,
        descripcion_ot,
        empresa_id,
    )
    return orden_id


def _crear_ot_mantenimiento_desde_parte(conn, parte, incidencia_id, descripcion, empresa_id):
    if parte["mantenimiento_orden_id"]:
        return parte["mantenimiento_orden_id"]
    contexto = _contexto_operativo_parte(conn, parte, empresa_id)
    equipo_id = _equipo_mantenimiento_pisos_contexto(conn, empresa_id, contexto, descripcion)
    numero_orden = _numero_ot_pisos(conn)
    destino = _habitacion_label(parte)
    detalle = descripcion or "Aviso creado desde pisos."
    descripcion_ot = (
        f"Aviso desde pisos.\n"
        f"Destino: {destino}\n"
        f"Tipo parte: {TIPOS_PARTE.get(parte['tipo'], parte['tipo'])}\n"
        f"Fecha parte: {parte['fecha']}\n"
        f"Detalle: {detalle}\n"
        f"Parte pisos #{parte['id']}"
    )
    cursor = conn.execute(
        """
        INSERT INTO ordenes_trabajo
        (numero_orden, equipo_id, tipo, prioridad, estado, descripcion,
         tecnico_asignado, incidencia_id, observaciones, origen_modulo, origen_tipo,
         origen_id, departamento_id, subdepartamento_id, punto_venta_id, ubicacion,
         habitacion_id, zona_id, requiere_appcc, empresa_id)
        VALUES (?, ?, 'correctivo', ?, 'abierta', ?, '', ?, ?, 'pisos', 'parte',
                ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            numero_orden,
            equipo_id,
            _prioridad_mantenimiento(parte["prioridad"]),
            descripcion_ot,
            incidencia_id,
            f"Origen: pisos. Parte #{parte['id']}.",
            parte["id"],
            contexto["departamento_id"],
            contexto["subdepartamento_id"],
            contexto["punto_venta_id"],
            contexto["ubicacion"],
            contexto["habitacion_id"],
            contexto["zona_id"],
            empresa_id,
        ),
    )
    orden_id = cursor.lastrowid
    conn.execute(
        """
        UPDATE incidencias
        SET orden_trabajo_id = ?, equipo_mantenimiento_id = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (orden_id, equipo_id, incidencia_id, empresa_id),
    )
    conn.execute(
        """
        UPDATE pisos_partes_limpieza
        SET mantenimiento_orden_id = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (orden_id, parte["id"], empresa_id),
    )
    _registrar_traza(conn, "parte", parte["id"], "crear_ot_mantenimiento", "", numero_orden, descripcion_ot, empresa_id)
    return orden_id


def _crear_incidencia_trabajo(conn, trabajo, descripcion, empresa_id, tipo_incidencia="mantenimiento", gravedad="media"):
    if not descripcion:
        descripcion = "Incidencia comunicada desde pisos."
    tipo_incidencia = tipo_incidencia if tipo_incidencia in TIPOS_INCIDENCIA_PISOS else "mantenimiento"
    gravedad = gravedad if gravedad in GRAVEDADES_INCIDENCIA else "media"
    ensure_incidencias_context_schema(conn)
    contexto = _contexto_operativo_trabajo(conn, trabajo, empresa_id)
    destino_modulo = "mantenimiento" if tipo_incidencia in INCIDENCIAS_A_MANTENIMIENTO_PISOS else "pisos"
    cursor = conn.execute(
        """
        INSERT INTO incidencias
        (tipo, referencia_id, usuario, descripcion, gravedad, estado, observaciones,
         origen_modulo, destino_modulo, categoria_operativa, departamento_id,
         subdepartamento_id, punto_venta_id, ubicacion, habitacion_id, zona_id,
         requiere_mantenimiento, requiere_appcc, origen_tabla, origen_id, empresa_id)
        VALUES (?, ?, ?, ?, ?, 'abierta', ?, 'pisos', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'pisos_trabajos', ?, ?)
        """,
        (
            f"pisos_{tipo_incidencia}",
            trabajo["id"],
            _usuario_nombre(),
            descripcion,
            gravedad,
            f"Trabajo {_destino_trabajo(trabajo)}",
            destino_modulo,
            tipo_incidencia,
            contexto["departamento_id"],
            contexto["subdepartamento_id"],
            contexto["punto_venta_id"],
            contexto["ubicacion"],
            contexto["habitacion_id"],
            contexto["zona_id"],
            1 if tipo_incidencia in INCIDENCIAS_A_MANTENIMIENTO_PISOS else 0,
            trabajo["id"],
            empresa_id,
        ),
    )
    incidencia_id = cursor.lastrowid
    _registrar_traza(
        conn,
        "trabajo",
        trabajo["id"],
        "crear_incidencia",
        "",
        str(incidencia_id),
        descripcion,
        empresa_id,
    )
    return incidencia_id


def _appcc_plan_trabajo(conn, empresa_id, trabajo):
    area = "Habitaciones" if trabajo["target_tipo"] == "habitacion" else "Zonas comunes"
    tarea = f"Pisos - {trabajo['tarea_nombre']}"
    plan = conn.execute(
        """
        SELECT id
        FROM plan_limpieza
        WHERE empresa_id = ? AND area = ? AND tarea = ?
        LIMIT 1
        """,
        (empresa_id, area, tarea),
    ).fetchone()
    if plan:
        return plan["id"]
    cursor = conn.execute(
        """
        INSERT INTO plan_limpieza
        (area, tarea, frecuencia, responsable, productos, instrucciones, empresa_id)
        VALUES (?, ?, 'diaria', 'Pisos', ?, ?, ?)
        """,
        (
            area,
            tarea,
            trabajo["productos_previstos"] or "",
            trabajo["checklist"] or "Registro generado desde el parte de pisos.",
            empresa_id,
        ),
    )
    plan_id = cursor.lastrowid
    _registrar_traza(conn, "appcc_plan", plan_id, "crear_plan_appcc", "", "activo", f"{area}: {tarea}", empresa_id)
    return plan_id


def _registrar_appcc_trabajo(conn, trabajo, productos_usados, resultado, empresa_id):
    if trabajo["appcc_registro_id"]:
        return trabajo["appcc_registro_id"]
    plan_id = _appcc_plan_trabajo(conn, empresa_id, trabajo)
    observaciones = (
        f"{_destino_trabajo(trabajo)}. Trabajo #{trabajo['id']}. "
        f"Tarea: {trabajo['tarea_nombre']}."
    )
    if productos_usados:
        observaciones += f" Productos usados: {productos_usados}."
    if resultado:
        observaciones += f" Resultado: {resultado}."
    cursor = conn.execute(
        """
        INSERT INTO registros_limpieza (plan_id, fecha, realizada_por, observaciones, empresa_id)
        VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?)
        """,
        (plan_id, _usuario_nombre(), observaciones, empresa_id),
    )
    registro_id = cursor.lastrowid
    _registrar_traza(conn, "trabajo", trabajo["id"], "registrar_appcc", "", str(registro_id), observaciones, empresa_id)
    return registro_id


def _registrar_lenceria_trabajo(conn, trabajo, form, empresa_id):
    sabanas = int(form.get("sabanas_usadas") or trabajo["sabanas_previstas"] or 0)
    toallas = int(form.get("toallas_usadas") or trabajo["toallas_previstas"] or 0)
    alfombrines = int(form.get("alfombrines_usados") or trabajo["alfombrines_previstos"] or 0)
    lote = form.get("lavanderia_lote") or trabajo["lavanderia_lote"] or ""
    lavanderia_proveedor_id = _to_int(form.get("lavanderia_proveedor_id"), 0)
    lavanderia = None
    if lavanderia_proveedor_id:
        lavanderia = conn.execute(
            "SELECT * FROM pisos_lavanderias WHERE id = ? AND empresa_id = ?",
            (lavanderia_proveedor_id, empresa_id),
        ).fetchone()
    proveedor = form.get("proveedor_lavanderia") or (lavanderia["nombre"] if lavanderia else "") or trabajo["proveedor_lavanderia"] or ""
    circuito = form.get("circuito_lavanderia") or (lavanderia["tipo"] if lavanderia else "") or _circuito_lavanderia_default(conn, empresa_id)
    if circuito not in CIRCUITOS_LAVANDERIA:
        circuito = _circuito_lavanderia_default(conn, empresa_id)
    estado_lavanderia = form.get("estado_lavanderia") or ("preparada" if circuito == "externa" else "pendiente")
    if estado_lavanderia not in ESTADOS_LAVANDERIA:
        estado_lavanderia = "pendiente"
    productos_lavado = form.get("productos_lavado") or trabajo["productos_lavado"] or ""
    fecha_salida = form.get("fecha_salida") or ""
    fecha_retorno_prevista = form.get("fecha_retorno_prevista") or ""
    fecha_retorno = form.get("fecha_retorno") or ""
    kg_estimados = _to_float(form.get("kg_estimados"), 0)
    coste_estimado = _to_float(form.get("coste_estimado"), 0)
    coste_real = _to_float(form.get("coste_real"), 0)
    albaran = form.get("albaran_lavanderia") or ""
    lote_retorno = form.get("lote_retorno") or ""
    if not any([sabanas, toallas, alfombrines, lote, proveedor, productos_lavado]):
        return None
    existente = conn.execute(
        """
        SELECT id
        FROM pisos_lenceria_movimientos
        WHERE trabajo_id = ? AND empresa_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (trabajo["id"], empresa_id),
    ).fetchone()
    if existente:
        conn.execute(
            """
            UPDATE pisos_lenceria_movimientos
            SET fecha = ?, habitacion_id = ?, reserva_id = ?, sabanas = ?, toallas = ?,
                alfombrines = ?, lote_lavanderia = ?, proveedor_lavanderia = ?,
                circuito_lavanderia = ?, lavanderia_proveedor_id = ?, estado_lavanderia = ?,
                fecha_salida = ?, fecha_retorno_prevista = ?, fecha_retorno = ?,
                kg_estimados = ?, coste_estimado = ?, coste_real = ?,
                albaran_lavanderia = ?, lote_retorno = ?,
                productos_lavado = ?, observaciones = ?, usuario = ?,
                fecha_registro = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (
                trabajo["fecha"],
                trabajo["habitacion_id"],
                trabajo["reserva_id"],
                sabanas,
                toallas,
                alfombrines,
                lote,
                proveedor,
                circuito,
                lavanderia_proveedor_id or None,
                estado_lavanderia,
                fecha_salida,
                fecha_retorno_prevista,
                fecha_retorno,
                kg_estimados,
                coste_estimado,
                coste_real,
                albaran,
                lote_retorno,
                productos_lavado,
                form.get("resultado") or "",
                _usuario_nombre(),
                existente["id"],
                empresa_id,
            ),
        )
        conn.execute(
            """
            DELETE FROM pisos_lenceria_movimientos
            WHERE trabajo_id = ? AND empresa_id = ? AND id <> ?
            """,
            (trabajo["id"], empresa_id, existente["id"]),
        )
        _registrar_traza(
            conn,
            "trabajo",
            trabajo["id"],
            "actualizar_lenceria",
            "",
            str(existente["id"]),
            f"Sabanas {sabanas}; toallas {toallas}; alfombrines {alfombrines}; circuito {circuito}; lote {lote or '-'}",
            empresa_id,
        )
        return existente["id"]
    cursor = conn.execute(
        """
        INSERT INTO pisos_lenceria_movimientos
        (trabajo_id, fecha, habitacion_id, reserva_id, tipo, sabanas, toallas, alfombrines,
         lote_lavanderia, proveedor_lavanderia, circuito_lavanderia, lavanderia_proveedor_id,
         estado_lavanderia, fecha_salida, fecha_retorno_prevista, fecha_retorno,
         kg_estimados, coste_estimado, coste_real, albaran_lavanderia, lote_retorno,
         productos_lavado, observaciones, usuario, empresa_id)
        VALUES (?, ?, ?, ?, 'registro', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trabajo["id"],
            trabajo["fecha"],
            trabajo["habitacion_id"],
            trabajo["reserva_id"],
            sabanas,
            toallas,
            alfombrines,
            lote,
            proveedor,
            circuito,
            lavanderia_proveedor_id or None,
            estado_lavanderia,
            fecha_salida,
            fecha_retorno_prevista,
            fecha_retorno,
            kg_estimados,
            coste_estimado,
            coste_real,
            albaran,
            lote_retorno,
            productos_lavado,
            form.get("resultado") or "",
            _usuario_nombre(),
            empresa_id,
        ),
    )
    movimiento_id = cursor.lastrowid
    _registrar_traza(
        conn,
        "trabajo",
        trabajo["id"],
        "registrar_lenceria",
        "",
        str(movimiento_id),
        f"Sabanas {sabanas}; toallas {toallas}; alfombrines {alfombrines}; circuito {circuito}; lote {lote or '-'}",
        empresa_id,
    )
    return movimiento_id


@pisos_bp.route("/")
@login_required
@_require_pisos_gestion
def dashboard():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    fecha_txt = request.args.get("fecha") or date.today().isoformat()
    fecha_obj = _parse_date(fecha_txt, date.today())
    fecha_txt = fecha_obj.isoformat()
    estado = request.args.get("estado") or ""
    bloque = request.args.get("bloque") or ""
    planta = request.args.get("planta") or ""
    _generar_partes_dia(conn, empresa_id, fecha_txt)
    conn.commit()
    partes = _partes_query(conn, empresa_id, fecha_txt, estado=estado, bloque=bloque, planta=planta)
    resumen = _resumen_pisos(conn, empresa_id, fecha_txt)
    productos_pisos = _productos_pisos(conn, empresa_id)
    lavanderias = _lavanderias_pisos(conn, empresa_id)
    bloques, plantas = _opciones_filtro(conn, empresa_id)
    conn.close()
    return render_template(
        "pisos/dashboard.html",
        fecha=fecha_txt,
        partes=partes,
        resumen=resumen,
        estados=ESTADOS_PARTE,
        tipos=TIPOS_PARTE,
        tareas=TAREAS_PISOS,
        estados_habitacion=ESTADOS_HABITACION,
        tipos_cama=TIPOS_CAMA,
        tipos_aire=TIPOS_AIRE,
        tipos_incidencia=TIPOS_INCIDENCIA_PISOS,
        gravedades_incidencia=GRAVEDADES_INCIDENCIA,
        tareas_registro_limpieza=TAREAS_REGISTRO_LIMPIEZA_PISOS,
        productos_pisos=productos_pisos,
        lavanderias=lavanderias,
        circuitos_lavanderia=CIRCUITOS_LAVANDERIA,
        filtros={"estado": estado, "bloque": bloque, "planta": planta},
        bloques=bloques,
        plantas=plantas,
    )


@pisos_bp.route("/habitaciones")
@login_required
@_require_pisos_gestion
def habitaciones():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    fecha_txt = request.args.get("fecha") or date.today().isoformat()
    fecha_obj = _parse_date(fecha_txt, date.today())
    fecha_txt = fecha_obj.isoformat()
    bloque = request.args.get("bloque") or ""
    planta = request.args.get("planta") or ""
    _generar_partes_dia(conn, empresa_id, fecha_txt)
    conn.commit()
    habitaciones_data = _habitaciones_pisos(conn, empresa_id, fecha_txt, bloque=bloque, planta=planta)
    resumen = _resumen_pisos(conn, empresa_id, fecha_txt)
    bloques, plantas = _opciones_filtro(conn, empresa_id)
    conn.close()
    return render_template(
        "pisos/habitaciones.html",
        fecha=fecha_txt,
        habitaciones=habitaciones_data,
        resumen=resumen,
        estados_habitacion=ESTADOS_HABITACION,
        tipos_cama=TIPOS_CAMA,
        tipos_aire=TIPOS_AIRE,
        filtros={"bloque": bloque, "planta": planta},
        bloques=bloques,
        plantas=plantas,
    )


@pisos_bp.route("/habitaciones/<int:habitacion_id>/equipamiento", methods=["GET", "POST"])
@login_required
@_require_pisos_gestion
def equipamiento_habitacion(habitacion_id):
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    habitacion = conn.execute(
        """
        SELECT h.*, th.nombre AS tipo_nombre
        FROM habitaciones_recepcion h
        LEFT JOIN recepcion_tipos_habitacion th ON h.tipo_habitacion_id = th.id
        WHERE h.id = ? AND h.empresa_id = ?
        """,
        (habitacion_id, empresa_id),
    ).fetchone()
    if not habitacion:
        conn.close()
        abort(404)
    equipamiento = conn.execute(
        "SELECT * FROM habitaciones_equipamiento WHERE habitacion_id = ? AND empresa_id = ?",
        (habitacion_id, empresa_id),
    ).fetchone()
    fecha_txt = request.values.get("fecha") or date.today().isoformat()
    fecha_txt = (_parse_date(fecha_txt, date.today())).isoformat()
    if request.method == "POST":
        data = {
            "tipo_cama": request.form.get("tipo_cama") or "",
            "camas_matrimonio": _to_int(request.form.get("camas_matrimonio"), 0),
            "camas_individuales": _to_int(request.form.get("camas_individuales"), 0),
            "camas_juntas": 1 if request.form.get("camas_juntas") else 0,
            "sofa_cama": 1 if request.form.get("sofa_cama") else 0,
            "supletoria_posible": 1 if request.form.get("supletoria_posible") else 0,
            "supletorias_max": _to_int(request.form.get("supletorias_max"), 0),
            "cuna_posible": 1 if request.form.get("cuna_posible") else 0,
            "tv": 1 if request.form.get("tv") else 0,
            "minibar": 1 if request.form.get("minibar") else 0,
            "aire_tipo": request.form.get("aire_tipo") or "",
            "armario": 1 if request.form.get("armario") else 0,
            "mesas": _to_int(request.form.get("mesas"), 0),
            "sillas": _to_int(request.form.get("sillas"), 0),
            "mesillas": _to_int(request.form.get("mesillas"), 0),
            "tumbonas": _to_int(request.form.get("tumbonas"), 0),
            "tendedero": 1 if request.form.get("tendedero") else 0,
            "caja_fuerte": 1 if request.form.get("caja_fuerte") else 0,
            "terraza": 1 if request.form.get("terraza") else 0,
            "inventario_extra": request.form.get("inventario_extra") or "",
            "observaciones": request.form.get("observaciones") or "",
            "revisado_por": request.form.get("revisado_por") or _usuario_nombre(),
        }
        data["supletorias_max"] = max(data["supletorias_max"], 0)
        if not data["supletoria_posible"]:
            data["supletorias_max"] = 0
        detalle = (
            f"Cama {TIPOS_CAMA.get(data['tipo_cama'], data['tipo_cama'] or 'sin definir')}; "
            f"supletoria max {data['supletorias_max']}; aire {TIPOS_AIRE.get(data['aire_tipo'], data['aire_tipo'] or '-')}"
        )
        if equipamiento:
            conn.execute(
                """
                UPDATE habitaciones_equipamiento
                SET tipo_cama = ?, camas_matrimonio = ?, camas_individuales = ?, camas_juntas = ?,
                    sofa_cama = ?, supletoria_posible = ?, supletorias_max = ?, cuna_posible = ?,
                    tv = ?, minibar = ?, aire_tipo = ?, armario = ?, mesas = ?, sillas = ?,
                    mesillas = ?, tumbonas = ?, tendedero = ?, caja_fuerte = ?, terraza = ?,
                    inventario_extra = ?, observaciones = ?, revisado_por = ?, revisado_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE habitacion_id = ? AND empresa_id = ?
                """,
                (
                    data["tipo_cama"],
                    data["camas_matrimonio"],
                    data["camas_individuales"],
                    data["camas_juntas"],
                    data["sofa_cama"],
                    data["supletoria_posible"],
                    data["supletorias_max"],
                    data["cuna_posible"],
                    data["tv"],
                    data["minibar"],
                    data["aire_tipo"],
                    data["armario"],
                    data["mesas"],
                    data["sillas"],
                    data["mesillas"],
                    data["tumbonas"],
                    data["tendedero"],
                    data["caja_fuerte"],
                    data["terraza"],
                    data["inventario_extra"],
                    data["observaciones"],
                    data["revisado_por"],
                    habitacion_id,
                    empresa_id,
                ),
            )
            accion = "actualizar_equipamiento"
        else:
            conn.execute(
                """
                INSERT INTO habitaciones_equipamiento
                (habitacion_id, tipo_cama, camas_matrimonio, camas_individuales, camas_juntas,
                 sofa_cama, supletoria_posible, supletorias_max, cuna_posible, tv, minibar,
                 aire_tipo, armario, mesas, sillas, mesillas, tumbonas, tendedero, caja_fuerte,
                 terraza, inventario_extra, observaciones, revisado_por, revisado_at, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                """,
                (
                    habitacion_id,
                    data["tipo_cama"],
                    data["camas_matrimonio"],
                    data["camas_individuales"],
                    data["camas_juntas"],
                    data["sofa_cama"],
                    data["supletoria_posible"],
                    data["supletorias_max"],
                    data["cuna_posible"],
                    data["tv"],
                    data["minibar"],
                    data["aire_tipo"],
                    data["armario"],
                    data["mesas"],
                    data["sillas"],
                    data["mesillas"],
                    data["tumbonas"],
                    data["tendedero"],
                    data["caja_fuerte"],
                    data["terraza"],
                    data["inventario_extra"],
                    data["observaciones"],
                    data["revisado_por"],
                    empresa_id,
                ),
            )
            accion = "crear_equipamiento"
        _registrar_traza(
            conn,
            "habitacion_equipamiento",
            habitacion_id,
            accion,
            "",
            "ok",
            detalle,
            empresa_id,
        )
        conn.commit()
        conn.close()
        flash("Equipamiento de habitacion guardado.", "success")
        return redirect(url_for("pisos.habitaciones", fecha=fecha_txt))
    conn.close()
    return render_template(
        "pisos/equipamiento_habitacion.html",
        habitacion=habitacion,
        equipamiento=equipamiento,
        tipos_cama=TIPOS_CAMA,
        tipos_aire=TIPOS_AIRE,
        fecha=fecha_txt,
    )


@pisos_bp.route("/trazabilidad")
@login_required
@_require_pisos_gestion
def trazabilidad():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    hoy = date.today().isoformat()
    desde = request.args.get("desde") or hoy
    hasta = request.args.get("hasta") or desde
    desde = (_parse_date(desde, date.today())).isoformat()
    hasta = (_parse_date(hasta, _parse_date(desde, date.today()))).isoformat()
    entidad = request.args.get("entidad") or ""
    q = (request.args.get("q") or "").strip()
    trazas = _trazabilidad_query(conn, empresa_id, desde, hasta, entidad=entidad, q=q)
    conn.close()
    return render_template(
        "pisos/trazabilidad.html",
        trazas=trazas,
        filtros={"desde": desde, "hasta": hasta, "entidad": entidad, "q": q},
        entidades={"": "Todas", "trabajo": "Trabajos", "parte": "Partes antiguas", "habitacion": "Habitaciones", "habitacion_equipamiento": "Equipamiento", "zona": "Zonas", "producto": "Productos", "consumo": "Consumos", "pedido": "Pedidos", "objeto_perdido": "Objetos perdidos", "dia": "Generacion", "appcc_plan": "APPCC"},
    )


@pisos_bp.route("/panel")
@login_required
@_require_pisos_gestion
def panel():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    fecha_txt = request.args.get("fecha") or date.today().isoformat()
    fecha_txt = (_parse_date(fecha_txt, date.today())).isoformat()
    _generar_partes_dia(conn, empresa_id, fecha_txt)
    conn.commit()
    panel_data = _panel_pisos(conn, empresa_id, fecha_txt)
    ultimas_incidencias = conn.execute(
        """
        SELECT *
        FROM incidencias
        WHERE empresa_id = ? AND COALESCE(tipo, '') LIKE 'pisos%'
        ORDER BY datetime(fecha) DESC, id DESC
        LIMIT 8
        """,
        (empresa_id,),
    ).fetchall()
    ultimos_objetos = conn.execute(
        """
        SELECT o.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM pisos_objetos_perdidos o
        LEFT JOIN habitaciones_recepcion h ON o.habitacion_id = h.id
        WHERE o.empresa_id = ?
        ORDER BY date(o.fecha) DESC, o.id DESC
        LIMIT 8
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "pisos/panel.html",
        fecha=fecha_txt,
        panel=panel_data,
        ultimas_incidencias=ultimas_incidencias,
        ultimos_objetos=ultimos_objetos,
        estados=ESTADOS_PARTE,
        estados_habitacion=ESTADOS_HABITACION,
    )


@pisos_bp.route("/productos", methods=["GET", "POST"])
@login_required
@_require_pisos_gestion
def productos():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        accion = request.form.get("accion") or "crear"
        if accion == "crear":
            producto_id = request.form.get("producto_id") or ""
            nombre = (request.form.get("nombre") or "").strip()
            unidad = (request.form.get("unidad") or "ud").strip() or "ud"
            if producto_id.isdigit() and not nombre and _table_exists(conn, "productos"):
                producto_global = conn.execute(
                    "SELECT nombre, unidad FROM productos WHERE id = ? AND empresa_id = ?",
                    (int(producto_id), empresa_id),
                ).fetchone()
                if producto_global:
                    nombre = producto_global["nombre"]
                    unidad = producto_global["unidad"] or unidad
            if nombre:
                cursor = conn.execute(
                    """
                    INSERT INTO pisos_productos
                    (producto_id, nombre, categoria, unidad, stock_actual, stock_minimo,
                     proveedor, ubicacion, activo, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        int(producto_id) if producto_id.isdigit() else None,
                        nombre,
                        request.form.get("categoria") or "otro",
                        unidad,
                        _to_float(request.form.get("stock_actual"), 0),
                        _to_float(request.form.get("stock_minimo"), 0),
                        request.form.get("proveedor") or "",
                        request.form.get("ubicacion") or "",
                        empresa_id,
                    ),
                )
                _registrar_traza(conn, "producto", cursor.lastrowid, "crear_producto", "", "activo", nombre, empresa_id)
                flash("Producto de pisos creado.", "success")
        elif accion == "stock":
            producto_id = _to_int(request.form.get("pisos_producto_id"), 0)
            producto = conn.execute(
                "SELECT * FROM pisos_productos WHERE id = ? AND empresa_id = ?",
                (producto_id, empresa_id),
            ).fetchone()
            if producto:
                nuevo_stock = _to_float(request.form.get("stock_actual"), producto["stock_actual"] or 0)
                conn.execute(
                    """
                    UPDATE pisos_productos
                    SET stock_actual = ?, stock_minimo = ?, proveedor = ?, ubicacion = ?,
                        unidad = ?, categoria = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND empresa_id = ?
                    """,
                    (
                        nuevo_stock,
                        _to_float(request.form.get("stock_minimo"), producto["stock_minimo"] or 0),
                        request.form.get("proveedor") or producto["proveedor"] or "",
                        request.form.get("ubicacion") or producto["ubicacion"] or "",
                        request.form.get("unidad") or producto["unidad"] or "ud",
                        request.form.get("categoria") or producto["categoria"] or "otro",
                        producto_id,
                        empresa_id,
                    ),
                )
                _registrar_traza(conn, "producto", producto_id, "actualizar_stock", str(producto["stock_actual"] or 0), str(nuevo_stock), producto["nombre"], empresa_id)
                flash("Stock actualizado.", "success")
        elif accion == "toggle":
            producto_id = _to_int(request.form.get("pisos_producto_id"), 0)
            producto = conn.execute(
                "SELECT * FROM pisos_productos WHERE id = ? AND empresa_id = ?",
                (producto_id, empresa_id),
            ).fetchone()
            if producto:
                nuevo = 0 if producto["activo"] else 1
                conn.execute(
                    "UPDATE pisos_productos SET activo = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
                    (nuevo, producto_id, empresa_id),
                )
                _registrar_traza(conn, "producto", producto_id, "cambiar_activo", str(producto["activo"]), str(nuevo), producto["nombre"], empresa_id)
        conn.commit()
        conn.close()
        return redirect(url_for("pisos.productos"))
    q = (request.args.get("q") or "").strip()
    categoria = request.args.get("categoria") or ""
    filtros = ["empresa_id = ?"]
    params = [empresa_id]
    if q:
        filtros.append("LOWER(nombre) LIKE ?")
        params.append(f"%{q.lower()}%")
    if categoria:
        filtros.append("categoria = ?")
        params.append(categoria)
    productos_rows = conn.execute(
        f"""
        SELECT *
        FROM pisos_productos
        WHERE {" AND ".join(filtros)}
        ORDER BY COALESCE(activo, 1) DESC, categoria, nombre
        """,
        params,
    ).fetchall()
    globales = _productos_globales_limpieza(conn, empresa_id)
    conn.close()
    return render_template(
        "pisos/productos.html",
        productos=productos_rows,
        productos_globales=globales,
        categorias=CATEGORIAS_PRODUCTOS_PISOS,
        filtros={"q": q, "categoria": categoria},
    )


@pisos_bp.route("/consumos")
@login_required
@_require_pisos_gestion
def consumos():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    hoy = date.today().isoformat()
    desde = request.args.get("desde") or hoy
    hasta = request.args.get("hasta") or desde
    desde = (_parse_date(desde, date.today())).isoformat()
    hasta = (_parse_date(hasta, _parse_date(desde, date.today()))).isoformat()
    categoria = request.args.get("categoria") or ""
    filtros = ["c.empresa_id = ?", "date(c.fecha) BETWEEN date(?) AND date(?)"]
    params = [empresa_id, desde, hasta]
    if categoria:
        filtros.append("c.categoria = ?")
        params.append(categoria)
    movimientos = conn.execute(
        f"""
        SELECT c.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               z.nombre AS zona_nombre, t.tarea_nombre
        FROM pisos_consumos c
        LEFT JOIN habitaciones_recepcion h ON c.habitacion_id = h.id
        LEFT JOIN pisos_zonas z ON c.zona_id = z.id
        LEFT JOIN pisos_trabajos t ON c.trabajo_id = t.id
        WHERE {" AND ".join(filtros)}
        ORDER BY date(c.fecha) DESC, c.id DESC
        """,
        params,
    ).fetchall()
    resumen = conn.execute(
        f"""
        SELECT producto_nombre, unidad, SUM(COALESCE(cantidad, 0)) AS cantidad
        FROM pisos_consumos c
        WHERE {" AND ".join(filtros)}
        GROUP BY producto_nombre, unidad
        ORDER BY producto_nombre
        """,
        params,
    ).fetchall()
    if request.args.get("imprimir"):
        filas = []
        for movimiento in movimientos:
            destino = movimiento["habitacion_numero"] or movimiento["habitacion_nombre"] or movimiento["zona_nombre"] or "-"
            filas.append(
                [
                    movimiento["fecha"],
                    destino,
                    movimiento["tarea_nombre"] or "-",
                    movimiento["producto_nombre"],
                    f"{(movimiento['cantidad'] or 0):.2f} {movimiento['unidad'] or ''}".strip(),
                    movimiento["usuario"] or "-",
                ]
            )
        resumen_print = [
            f"{row['producto_nombre']}: {(row['cantidad'] or 0):.2f} {row['unidad'] or ''}".strip()
            for row in resumen
        ]
        conn.close()
        return _render_pisos_registro_print(
            "Consumos de pisos",
            {"Desde": desde, "Hasta": hasta, "Categoria": CATEGORIAS_PRODUCTOS_PISOS.get(categoria, categoria or "Todas")},
            ["Fecha", "Destino", "Trabajo", "Producto", "Cantidad", "Usuario"],
            filas,
            resumen_print,
        )
    conn.close()
    return render_template(
        "pisos/consumos.html",
        movimientos=movimientos,
        resumen=resumen,
        categorias=CATEGORIAS_PRODUCTOS_PISOS,
        filtros={"desde": desde, "hasta": hasta, "categoria": categoria},
    )


@pisos_bp.route("/pedidos", methods=["GET", "POST"])
@login_required
@_require_pisos_gestion
def pedidos():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        accion = request.form.get("accion") or "crear"
        if accion == "crear":
            producto_ids = [int(x) for x in request.form.getlist("producto_id") if str(x).isdigit()]
            if not producto_ids and request.form.get("desde_minimos"):
                producto_ids = [p["id"] for p in _productos_bajo_minimo_pisos(conn, empresa_id)]
            if producto_ids:
                cursor = conn.execute(
                    """
                    INSERT INTO pisos_pedidos_internos
                    (fecha, estado, prioridad, solicitante, observaciones, empresa_id)
                    VALUES (?, 'pendiente', ?, ?, ?, ?)
                    """,
                    (
                        date.today().isoformat(),
                        request.form.get("prioridad") or "media",
                        _usuario_nombre(),
                        request.form.get("observaciones") or "",
                        empresa_id,
                    ),
                )
                pedido_id = cursor.lastrowid
                for producto_id in producto_ids:
                    producto = conn.execute(
                        "SELECT * FROM pisos_productos WHERE id = ? AND empresa_id = ?",
                        (producto_id, empresa_id),
                    ).fetchone()
                    if not producto:
                        continue
                    cantidad = _to_float(request.form.get(f"cantidad_{producto_id}"), 0)
                    if cantidad <= 0:
                        cantidad = max((producto["stock_minimo"] or 0) - (producto["stock_actual"] or 0), 1)
                    conn.execute(
                        """
                        INSERT INTO pisos_pedidos_lineas
                        (pedido_id, producto_pisos_id, producto_id, nombre, cantidad, unidad, estado, empresa_id)
                        VALUES (?, ?, ?, ?, ?, ?, 'pendiente', ?)
                        """,
                        (pedido_id, producto["id"], producto["producto_id"], producto["nombre"], cantidad, producto["unidad"] or "ud", empresa_id),
                    )
                _registrar_traza(conn, "pedido", pedido_id, "crear_pedido_interno", "", "pendiente", f"{len(producto_ids)} lineas", empresa_id)
                flash("Pedido interno de pisos creado.", "success")
            else:
                flash("Selecciona algun producto o usa los bajo minimo.", "warning")
        elif accion == "estado":
            pedido_id = _to_int(request.form.get("pedido_id"), 0)
            estado = request.form.get("estado") or "pendiente"
            if estado not in {"pendiente", "solicitado", "aprobado", "recibido", "cerrado", "cancelado"}:
                estado = "pendiente"
            pedido = conn.execute(
                "SELECT * FROM pisos_pedidos_internos WHERE id = ? AND empresa_id = ?",
                (pedido_id, empresa_id),
            ).fetchone()
            if pedido:
                conn.execute(
                    "UPDATE pisos_pedidos_internos SET estado = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
                    (estado, pedido_id, empresa_id),
                )
                _registrar_traza(conn, "pedido", pedido_id, "cambiar_estado", pedido["estado"] or "", estado, pedido["observaciones"] or "", empresa_id)
        conn.commit()
        conn.close()
        return redirect(url_for("pisos.pedidos"))
    productos_rows = _productos_pisos(conn, empresa_id)
    bajo_minimo = _productos_bajo_minimo_pisos(conn, empresa_id)
    pedidos_rows = conn.execute(
        """
        SELECT p.*,
               COUNT(l.id) AS total_lineas,
               GROUP_CONCAT(l.nombre || ' ' || l.cantidad || ' ' || COALESCE(l.unidad, ''), '; ') AS lineas
        FROM pisos_pedidos_internos p
        LEFT JOIN pisos_pedidos_lineas l ON p.id = l.pedido_id
        WHERE p.empresa_id = ?
        GROUP BY p.id
        ORDER BY date(p.fecha) DESC, p.id DESC
        LIMIT 80
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "pisos/pedidos.html",
        productos=productos_rows,
        bajo_minimo=bajo_minimo,
        pedidos=pedidos_rows,
    )


@pisos_bp.route("/objetos-perdidos", methods=["GET", "POST"])
@login_required
@_require_pisos
def objetos_perdidos():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    puede_gestionar = _puede_pisos_gestionar()
    if request.method == "POST":
        accion = request.form.get("accion") or "crear"
        if accion == "crear":
            descripcion = (request.form.get("descripcion") or "").strip()
            if descripcion:
                habitacion_id = request.form.get("habitacion_id") or ""
                reserva_id = request.form.get("reserva_id") or ""
                cursor = conn.execute(
                    """
                    INSERT INTO pisos_objetos_perdidos
                    (fecha, habitacion_id, reserva_id, descripcion, categoria, encontrado_por,
                     ubicacion_guardado, estado, observaciones, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'guardado', ?, ?)
                    """,
                    (
                        request.form.get("fecha") or date.today().isoformat(),
                        int(habitacion_id) if habitacion_id.isdigit() else None,
                        int(reserva_id) if reserva_id.isdigit() else None,
                        descripcion,
                        request.form.get("categoria") or "",
                        request.form.get("encontrado_por") or _nombre_trabajador_sesion(conn, empresa_id) or _usuario_nombre(),
                        request.form.get("ubicacion_guardado") or "Office pisos",
                        request.form.get("observaciones") or "",
                        empresa_id,
                    ),
                )
                _registrar_traza(conn, "objeto_perdido", cursor.lastrowid, "crear_objeto", "", "guardado", descripcion, empresa_id)
                flash("Objeto perdido registrado.", "success")
        elif accion == "estado":
            objeto_id = _to_int(request.form.get("objeto_id"), 0)
            estado = request.form.get("estado") or "guardado"
            if estado not in ESTADOS_OBJETO_PERDIDO:
                estado = "guardado"
            objeto = conn.execute(
                "SELECT * FROM pisos_objetos_perdidos WHERE id = ? AND empresa_id = ?",
                (objeto_id, empresa_id),
            ).fetchone()
            if objeto and puede_gestionar:
                conn.execute(
                    """
                    UPDATE pisos_objetos_perdidos
                    SET estado = ?, entregado_a = ?, documento_entrega = ?,
                        fecha_entrega = ?, observaciones = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND empresa_id = ?
                    """,
                    (
                        estado,
                        request.form.get("entregado_a") or objeto["entregado_a"] or "",
                        request.form.get("documento_entrega") or objeto["documento_entrega"] or "",
                        request.form.get("fecha_entrega") or (date.today().isoformat() if estado == "entregado" else objeto["fecha_entrega"]),
                        request.form.get("observaciones") or objeto["observaciones"] or "",
                        objeto_id,
                        empresa_id,
                    ),
                )
                _registrar_traza(conn, "objeto_perdido", objeto_id, "cambiar_estado", objeto["estado"] or "", estado, objeto["descripcion"], empresa_id)
                flash("Objeto perdido actualizado.", "success")
            elif objeto:
                flash("Solo gobernanta o administracion puede cambiar el estado.", "warning")
        conn.commit()
        conn.close()
        return redirect(url_for("pisos.objetos_perdidos"))
    desde = request.args.get("desde") or date.today().isoformat()
    hasta = request.args.get("hasta") or desde
    desde = (_parse_date(desde, date.today())).isoformat()
    hasta = (_parse_date(hasta, _parse_date(desde, date.today()))).isoformat()
    estado = request.args.get("estado") or ""
    filtros = ["o.empresa_id = ?", "date(o.fecha) BETWEEN date(?) AND date(?)"]
    params = [empresa_id, desde, hasta]
    if estado:
        filtros.append("o.estado = ?")
        params.append(estado)
    objetos = conn.execute(
        f"""
        SELECT o.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               r.codigo AS reserva_codigo, r.titular_nombre
        FROM pisos_objetos_perdidos o
        LEFT JOIN habitaciones_recepcion h ON o.habitacion_id = h.id
        LEFT JOIN reservas_recepcion r ON o.reserva_id = r.id
        WHERE {" AND ".join(filtros)}
        ORDER BY date(o.fecha) DESC, o.id DESC
        """,
        params,
    ).fetchall()
    habitaciones = conn.execute(
        """
        SELECT id, numero, nombre
        FROM habitaciones_recepcion
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY bloque, planta, numero, nombre
        """,
        (empresa_id,),
    ).fetchall()
    if request.args.get("imprimir"):
        filas = []
        for objeto in objetos:
            habitacion = objeto["habitacion_numero"] or objeto["habitacion_nombre"] or "-"
            reserva = objeto["reserva_codigo"] or objeto["titular_nombre"] or "-"
            filas.append(
                [
                    objeto["fecha"],
                    habitacion,
                    reserva,
                    objeto["descripcion"],
                    objeto["categoria"] or "-",
                    objeto["ubicacion_guardado"] or "-",
                    ESTADOS_OBJETO_PERDIDO.get(objeto["estado"], objeto["estado"] or "guardado"),
                    objeto["entregado_a"] or "-",
                ]
            )
        conn.close()
        return _render_pisos_registro_print(
            "Objetos perdidos",
            {"Desde": desde, "Hasta": hasta, "Estado": ESTADOS_OBJETO_PERDIDO.get(estado, estado or "Todos")},
            ["Fecha", "Habitacion", "Reserva", "Objeto", "Categoria", "Custodia", "Estado", "Entregado a"],
            filas,
        )
    conn.close()
    return render_template(
        "pisos/objetos_perdidos.html",
        objetos=objetos,
        habitaciones=habitaciones,
        estados=ESTADOS_OBJETO_PERDIDO,
        puede_gestionar=puede_gestionar,
        filtros={"desde": desde, "hasta": hasta, "estado": estado},
    )


@pisos_bp.route("/generar", methods=["POST"])
@login_required
@_require_pisos_gestion
def generar_partes():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    fecha_txt = request.form.get("fecha") or date.today().isoformat()
    fecha_txt = (_parse_date(fecha_txt, date.today())).isoformat()
    creados = _generar_partes_dia(conn, empresa_id, fecha_txt)
    _registrar_traza(
        conn,
        "dia",
        None,
        "generar_partes",
        "",
        str(creados),
        f"Fecha {fecha_txt}. Tareas nuevas: {creados}",
        empresa_id,
    )
    conn.commit()
    conn.close()
    flash(f"Parte de pisos actualizado. Tareas nuevas: {creados}.", "success")
    return redirect(url_for("pisos.dashboard", fecha=fecha_txt))


@pisos_bp.route("/asignacion", methods=["GET", "POST"])
@login_required
@_require_pisos_gestion
def asignacion():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    fecha_txt = request.values.get("fecha") or date.today().isoformat()
    fecha_txt = (_parse_date(fecha_txt, date.today())).isoformat()
    bloque = request.values.get("bloque") or ""
    planta = request.values.get("planta") or ""
    if request.method == "POST":
        accion = request.form.get("accion") or "asignar"
        if accion == "nuevo_personal":
            nombre = (request.form.get("nombre") or "").strip()
            if nombre:
                cursor = conn.execute(
                    """
                    INSERT INTO pisos_personal (nombre, rol, telefono, empresa_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        nombre,
                        request.form.get("rol") or "camarera",
                        request.form.get("telefono") or "",
                        empresa_id,
                    ),
                )
                _registrar_traza(conn, "personal", cursor.lastrowid, "crear_personal", "", "activo", nombre, empresa_id)
                flash("Personal de pisos creado.", "success")
        elif accion == "asignar":
            trabajo_ids = [int(x) for x in request.form.getlist("trabajo_ids") if str(x).isdigit()]
            personal_id = request.form.get("personal_id") or ""
            personal = None
            if personal_id.isdigit():
                personal = conn.execute(
                    "SELECT * FROM pisos_personal WHERE id = ? AND empresa_id = ?",
                    (int(personal_id), empresa_id),
                ).fetchone()
            asignado_a = (personal["nombre"] if personal else request.form.get("asignado_a")) or ""
            empleado_id = personal["empleado_id"] if personal else None
            if trabajo_ids and asignado_a:
                placeholders = ",".join("?" for _ in trabajo_ids)
                conn.execute(
                    f"""
                    UPDATE pisos_trabajos
                    SET asignado_a = ?, asignado_empleado_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE empresa_id = ? AND id IN ({placeholders})
                    """,
                    [asignado_a, empleado_id, empresa_id] + trabajo_ids,
                )
                for trabajo_id in trabajo_ids:
                    _registrar_traza(conn, "trabajo", trabajo_id, "asignar", "", asignado_a, "Asignacion manual", empresa_id)
                flash(f"Trabajos asignados a {asignado_a}.", "success")
        elif accion == "auto":
            personal = _personal_pisos(conn, empresa_id)
            if personal:
                trabajos = _partes_query(conn, empresa_id, fecha_txt, estado="pendiente", bloque=bloque, planta=planta)
                for idx, trabajo in enumerate(trabajos):
                    persona = personal[idx % len(personal)]
                    conn.execute(
                        """
                        UPDATE pisos_trabajos
                        SET asignado_a = ?, asignado_empleado_id = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND empresa_id = ?
                        """,
                        (persona["nombre"], persona["empleado_id"], trabajo["id"], empresa_id),
                    )
                    _registrar_traza(conn, "trabajo", trabajo["id"], "asignar_auto", "", persona["nombre"], "Reparto automatico", empresa_id)
                flash(f"Reparto automatico realizado: {len(trabajos)} trabajos.", "success")
            else:
                flash("Primero crea personal de pisos.", "warning")
        conn.commit()
        conn.close()
        return redirect(url_for("pisos.asignacion", fecha=fecha_txt, bloque=bloque, planta=planta))

    _generar_partes_dia(conn, empresa_id, fecha_txt)
    conn.commit()
    trabajos = _partes_query(conn, empresa_id, fecha_txt, bloque=bloque, planta=planta)
    personal = _personal_pisos(conn, empresa_id)
    resumen_personal = conn.execute(
        """
        SELECT COALESCE(asignado_a, 'Sin asignar') AS asignado_a,
               COUNT(*) AS total,
               SUM(CASE WHEN estado = 'hecha' THEN 1 ELSE 0 END) AS hechas,
               SUM(CASE WHEN estado = 'pendiente' THEN 1 ELSE 0 END) AS pendientes
        FROM pisos_trabajos
        WHERE empresa_id = ? AND fecha = ?
        GROUP BY COALESCE(asignado_a, 'Sin asignar')
        ORDER BY asignado_a
        """,
        (empresa_id, fecha_txt),
    ).fetchall()
    bloques, plantas = _opciones_filtro(conn, empresa_id)
    conn.close()
    return render_template(
        "pisos/asignacion.html",
        fecha=fecha_txt,
        trabajos=trabajos,
        personal=personal,
        resumen_personal=resumen_personal,
        filtros={"bloque": bloque, "planta": planta},
        bloques=bloques,
        plantas=plantas,
    )


@pisos_bp.route("/tablet")
@login_required
@_require_pisos
def tablet():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    fecha_txt = request.args.get("fecha") or date.today().isoformat()
    fecha_txt = (_parse_date(fecha_txt, date.today())).isoformat()
    puede_gestionar = _puede_pisos_gestionar()
    trabajador = (request.args.get("trabajador") or "").strip()
    estado = request.args.get("estado") or ""
    _generar_partes_dia(conn, empresa_id, fecha_txt)
    conn.commit()
    filtros = ["p.empresa_id = ?", "p.fecha = ?"]
    params = [empresa_id, fecha_txt]
    empleado_id = session.get("user_empleado_id") or 0
    if not puede_gestionar:
        trabajador = _nombre_trabajador_sesion(conn, empresa_id)
        if empleado_id:
            filtros.append("(COALESCE(p.asignado_empleado_id, 0) = ? OR COALESCE(p.asignado_a, '') = ?)")
            params.extend([empleado_id, trabajador])
        elif trabajador:
            filtros.append("COALESCE(p.asignado_a, '') = ?")
            params.append(trabajador)
        else:
            filtros.append("1 = 0")
    elif trabajador:
        filtros.append("COALESCE(p.asignado_a, '') = ?")
        params.append(trabajador)
    if estado:
        filtros.append("p.estado = ?")
        params.append(estado)
    trabajos = conn.execute(
        f"""
        SELECT p.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               eq.tipo_cama, eq.supletoria_posible, eq.supletorias_max, eq.cuna_posible,
               eq.tv, eq.minibar, eq.aire_tipo, eq.inventario_extra, eq.observaciones AS equipamiento_observaciones,
               z.nombre AS zona_nombre, z.appcc_critico AS zona_appcc_critico,
               z.departamento_id AS zona_departamento_id,
               z.subdepartamento_id AS zona_subdepartamento_id,
               z.punto_venta_id AS zona_punto_venta_id,
               r.adultos, r.menores,
               r.alergias_resumen, r.observaciones_operativas,
               r.preferencia_cama, r.supletoria_solicitada, r.cuna_solicitada, r.observaciones_habitacion
        FROM pisos_trabajos p
        LEFT JOIN habitaciones_recepcion h ON p.habitacion_id = h.id
        LEFT JOIN habitaciones_equipamiento eq ON h.id = eq.habitacion_id AND eq.empresa_id = p.empresa_id
        LEFT JOIN pisos_zonas z ON p.zona_id = z.id
        LEFT JOIN reservas_recepcion r ON p.reserva_id = r.id
        WHERE {" AND ".join(filtros)}
        ORDER BY CASE p.estado WHEN 'en_curso' THEN 1 WHEN 'pendiente' THEN 2 WHEN 'incidencia' THEN 3 ELSE 4 END,
                 CASE p.prioridad WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END,
                 p.bloque, p.planta, h.numero, z.nombre, p.id
        """,
        params,
    ).fetchall()
    personal = _personal_pisos(conn, empresa_id)
    productos_pisos = _productos_pisos(conn, empresa_id)
    lavanderias = _lavanderias_pisos(conn, empresa_id)
    resumen_tablet = {
        "total": len(trabajos),
        "pendiente": sum(1 for t in trabajos if t["estado"] == "pendiente"),
        "en_curso": sum(1 for t in trabajos if t["estado"] == "en_curso"),
        "hecha": sum(1 for t in trabajos if t["estado"] == "hecha"),
        "incidencia": sum(1 for t in trabajos if t["estado"] == "incidencia"),
    }
    conn.close()
    return render_template(
        "pisos/tablet.html",
        fecha=fecha_txt,
        trabajador=trabajador,
        estado=estado,
        trabajos=trabajos,
        personal=personal,
        resumen_tablet=resumen_tablet,
        puede_gestionar=puede_gestionar,
        estados=ESTADOS_PARTE,
        tipos_cama=TIPOS_CAMA,
        tipos_aire=TIPOS_AIRE,
        tipos_incidencia=TIPOS_INCIDENCIA_PISOS,
        gravedades_incidencia=GRAVEDADES_INCIDENCIA,
        tareas_registro_limpieza=TAREAS_REGISTRO_LIMPIEZA_PISOS,
        productos_pisos=productos_pisos,
        lavanderias=lavanderias,
        circuitos_lavanderia=CIRCUITOS_LAVANDERIA,
    )


@pisos_bp.route("/trabajos/<int:trabajo_id>/estado", methods=["POST"])
@login_required
@_require_pisos
def cambiar_estado_trabajo(trabajo_id):
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    trabajo = conn.execute(
        """
        SELECT t.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               h.estado AS habitacion_estado, z.nombre AS zona_nombre, z.tipo AS zona_tipo,
               z.appcc_critico AS zona_appcc_critico,
               z.departamento_id AS zona_departamento_id,
               z.subdepartamento_id AS zona_subdepartamento_id,
               z.punto_venta_id AS zona_punto_venta_id
        FROM pisos_trabajos t
        LEFT JOIN habitaciones_recepcion h ON t.habitacion_id = h.id
        LEFT JOIN pisos_zonas z ON t.zona_id = z.id
        WHERE t.id = ? AND t.empresa_id = ?
        """,
        (trabajo_id, empresa_id),
    ).fetchone()
    if not trabajo:
        conn.close()
        abort(404)
    if not _trabajo_asignado_a_usuario(trabajo, conn, empresa_id):
        conn.close()
        flash("Este trabajo esta asignado a otra persona.", "warning")
        return redirect(url_for("pisos.tablet"))
    estado = request.form.get("estado") or "pendiente"
    if estado not in ESTADOS_PARTE:
        estado = "pendiente"
    resultado = request.form.get("resultado") or trabajo["resultado"] or ""
    observaciones = request.form.get("observaciones") or trabajo["observaciones"] or ""
    asignado_a = request.form.get("asignado_a") or trabajo["asignado_a"] or ""
    if not _puede_pisos_gestionar() and not asignado_a:
        asignado_a = _nombre_trabajador_sesion(conn, empresa_id)
    productos_usados = request.form.get("productos_usados") or trabajo["productos_usados"] or ""
    lavanderia_lote = request.form.get("lavanderia_lote") or trabajo["lavanderia_lote"] or ""
    proveedor_lavanderia = request.form.get("proveedor_lavanderia") or trabajo["proveedor_lavanderia"] or ""
    productos_lavado = request.form.get("productos_lavado") or trabajo["productos_lavado"] or ""
    incidencia_tipo = request.form.get("incidencia_tipo") or trabajo["incidencia_tipo"] or "mantenimiento"
    if incidencia_tipo not in TIPOS_INCIDENCIA_PISOS:
        incidencia_tipo = "mantenimiento"
    incidencia_gravedad = request.form.get("incidencia_gravedad") or trabajo["incidencia_gravedad"] or trabajo["prioridad"] or "media"
    if incidencia_gravedad not in GRAVEDADES_INCIDENCIA:
        incidencia_gravedad = "media"
    sabanas_usadas = int(request.form.get("sabanas_usadas") or trabajo["sabanas_usadas"] or trabajo["sabanas_previstas"] or 0)
    toallas_usadas = int(request.form.get("toallas_usadas") or trabajo["toallas_usadas"] or trabajo["toallas_previstas"] or 0)
    alfombrines_usados = int(request.form.get("alfombrines_usados") or trabajo["alfombrines_usados"] or trabajo["alfombrines_previstos"] or 0)
    iniciado_at = trabajo["iniciado_at"]
    cerrado_at = trabajo["cerrado_at"]
    if estado == "en_curso" and not iniciado_at:
        iniciado_at = datetime.now().isoformat(timespec="seconds")
    if estado in {"hecha", "incidencia", "cancelada"} and not cerrado_at:
        cerrado_at = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE pisos_trabajos
        SET estado = ?, resultado = ?, observaciones = ?, asignado_a = ?,
            productos_usados = ?, sabanas_usadas = ?, toallas_usadas = ?,
            alfombrines_usados = ?, lavanderia_lote = ?, proveedor_lavanderia = ?,
            productos_lavado = ?, incidencia_tipo = ?, incidencia_gravedad = ?,
            iniciado_at = ?, cerrado_at = ?,
            cerrado_por = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (
            estado,
            resultado,
            observaciones,
            asignado_a,
            productos_usados,
            sabanas_usadas,
            toallas_usadas,
            alfombrines_usados,
            lavanderia_lote,
            proveedor_lavanderia,
            productos_lavado,
            incidencia_tipo,
            incidencia_gravedad,
            iniciado_at,
            cerrado_at,
            _usuario_id(),
            trabajo_id,
            empresa_id,
        ),
    )
    habitacion_estado_nuevo = None
    if trabajo["target_tipo"] == "habitacion" and trabajo["habitacion_id"]:
        if estado == "en_curso":
            habitacion_estado_nuevo = "limpieza"
        elif estado == "hecha":
            habitacion_estado_nuevo = request.form.get("habitacion_estado") or "disponible"
            if trabajo["tarea_codigo"] in {"repaso", "toallas", "sabanas", "bano_desinfeccion", "amenities"}:
                habitacion_estado_nuevo = "ocupada"
            if habitacion_estado_nuevo not in ESTADOS_HABITACION:
                habitacion_estado_nuevo = "disponible"
        elif estado == "incidencia":
            habitacion_estado_nuevo = "mantenimiento"
        if habitacion_estado_nuevo:
            conn.execute(
                "UPDATE habitaciones_recepcion SET estado = ? WHERE id = ? AND empresa_id = ?",
                (habitacion_estado_nuevo, trabajo["habitacion_id"], empresa_id),
            )
            _registrar_traza(
                conn,
                "habitacion",
                trabajo["habitacion_id"],
                "cambiar_estado",
                trabajo["habitacion_estado"] or "",
                habitacion_estado_nuevo,
                f"Por trabajo #{trabajo_id} ({trabajo['tarea_nombre']})",
                empresa_id,
            )
    if estado == "hecha":
        consumos = _registrar_consumos_trabajo(conn, trabajo, request.form, empresa_id)
        if consumos:
            detalle_consumos = "; ".join(consumos)
            if productos_usados and detalle_consumos not in productos_usados:
                productos_usados = f"{productos_usados}; stock: {detalle_consumos}"
            elif not productos_usados:
                productos_usados = detalle_consumos
            conn.execute(
                "UPDATE pisos_trabajos SET productos_usados = ? WHERE id = ? AND empresa_id = ?",
                (productos_usados, trabajo_id, empresa_id),
            )
        if _trabajo_requiere_registro_limpieza(trabajo) and _form_bool("registrar_appcc", default=True):
            appcc_registro_id = _registrar_appcc_trabajo(conn, trabajo, productos_usados, resultado, empresa_id)
            conn.execute(
                "UPDATE pisos_trabajos SET appcc_registro_id = ? WHERE id = ? AND empresa_id = ?",
                (appcc_registro_id, trabajo_id, empresa_id),
            )
        if trabajo["target_tipo"] == "habitacion":
            _registrar_lenceria_trabajo(conn, trabajo, request.form, empresa_id)
    elif estado == "incidencia":
        incidencia_id = _crear_incidencia_trabajo(
            conn,
            trabajo,
            observaciones or resultado,
            empresa_id,
            tipo_incidencia=incidencia_tipo,
            gravedad=incidencia_gravedad,
        )
        orden_id = trabajo["mantenimiento_orden_id"]
        if incidencia_tipo in INCIDENCIAS_A_MANTENIMIENTO_PISOS:
            orden_id = _crear_ot_mantenimiento_desde_trabajo(conn, trabajo, incidencia_id, observaciones or resultado, empresa_id)
        conn.execute(
            "UPDATE pisos_trabajos SET incidencia_id = ?, mantenimiento_orden_id = ? WHERE id = ? AND empresa_id = ?",
            (incidencia_id, orden_id, trabajo_id, empresa_id),
        )
    _registrar_traza(
        conn,
        "trabajo",
        trabajo_id,
        "cambiar_estado",
        trabajo["estado"] or "",
        estado,
        f"Asignado: {asignado_a or '-'}; productos: {productos_usados or '-'}; lenceria S{sabanas_usadas} T{toallas_usadas}",
        empresa_id,
    )
    conn.commit()
    fecha_txt = trabajo["fecha"]
    conn.close()
    flash("Trabajo actualizado.", "success")
    return redirect(request.referrer or url_for("pisos.dashboard", fecha=fecha_txt))


@pisos_bp.route("/trabajos/<int:trabajo_id>/validar", methods=["POST"])
@login_required
@_require_pisos_gestion
def validar_trabajo(trabajo_id):
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    trabajo = conn.execute(
        "SELECT * FROM pisos_trabajos WHERE id = ? AND empresa_id = ?",
        (trabajo_id, empresa_id),
    ).fetchone()
    if not trabajo:
        conn.close()
        abort(404)
    revision_estado = request.form.get("revision_estado") or "validado"
    if revision_estado not in {"validado", "rechazado"}:
        revision_estado = "validado"
    observaciones = request.form.get("revision_observaciones") or ""
    nuevo_estado = trabajo["estado"]
    if revision_estado == "rechazado":
        nuevo_estado = "pendiente"
    conn.execute(
        """
        UPDATE pisos_trabajos
        SET revision_estado = ?, revisado_por = ?, revisado_at = CURRENT_TIMESTAMP,
            revision_observaciones = ?, estado = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (revision_estado, _usuario_id(), observaciones, nuevo_estado, trabajo_id, empresa_id),
    )
    _registrar_traza(
        conn,
        "trabajo",
        trabajo_id,
        "validar",
        trabajo["revision_estado"] or "pendiente",
        revision_estado,
        observaciones or "Validacion de responsable",
        empresa_id,
    )
    conn.commit()
    fecha_txt = trabajo["fecha"]
    conn.close()
    flash("Revision registrada.", "success")
    return redirect(request.referrer or url_for("pisos.dashboard", fecha=fecha_txt))


@pisos_bp.route("/configuracion", methods=["GET", "POST"])
@login_required
@_require_pisos_gestion
def configuracion():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        accion = request.form.get("accion") or "config"
        if accion == "config":
            conn.execute(
                """
                UPDATE pisos_configuracion
                SET repaso_diario = ?, cambio_toallas_cada_dias = ?,
                    cambio_sabanas_cada_dias = ?, desinfeccion_bano_diaria = ?,
                    amenities_diarios = ?, modo_lavanderia = ?,
                    lavanderia_propia_nombre = ?, lavanderia_externa_principal = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE empresa_id = ?
                """,
                (
                    1 if request.form.get("repaso_diario") else 0,
                    int(request.form.get("cambio_toallas_cada_dias") or 1),
                    int(request.form.get("cambio_sabanas_cada_dias") or 3),
                    1 if request.form.get("desinfeccion_bano_diaria") else 0,
                    1 if request.form.get("amenities_diarios") else 0,
                    request.form.get("modo_lavanderia") if request.form.get("modo_lavanderia") in MODOS_LAVANDERIA else "mixta",
                    request.form.get("lavanderia_propia_nombre") or "Lavanderia propia",
                    request.form.get("lavanderia_externa_principal") or "Lavanderia externa habitual",
                    empresa_id,
                ),
            )
            _registrar_traza(conn, "configuracion", None, "actualizar_configuracion", "", "ok", "Reglas de pisos actualizadas.", empresa_id)
            flash("Reglas de pisos actualizadas.", "success")
        elif accion == "zona":
            nombre = (request.form.get("nombre") or "").strip()
            if nombre:
                cursor = conn.execute(
                    """
                    INSERT INTO pisos_zonas
                    (nombre, tipo, bloque, planta, frecuencia, prioridad, productos_previstos,
                     instrucciones, appcc_critico, activo, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        nombre,
                        request.form.get("tipo") or "zona_comun",
                        request.form.get("bloque") or "",
                        request.form.get("planta") or "",
                        request.form.get("frecuencia") or "diaria",
                        request.form.get("prioridad") or "media",
                        request.form.get("productos_previstos") or "",
                        request.form.get("instrucciones") or "",
                        1 if request.form.get("appcc_critico") else 0,
                        empresa_id,
                    ),
                )
                _registrar_traza(conn, "zona", cursor.lastrowid, "crear_zona", "", "activa", nombre, empresa_id)
                flash("Zona comun creada.", "success")
        elif accion == "toggle_zona":
            zona_id = int(request.form.get("zona_id") or 0)
            zona = conn.execute(
                "SELECT activo, nombre FROM pisos_zonas WHERE id = ? AND empresa_id = ?",
                (zona_id, empresa_id),
            ).fetchone()
            if zona:
                nuevo = 0 if zona["activo"] else 1
                conn.execute(
                    "UPDATE pisos_zonas SET activo = ? WHERE id = ? AND empresa_id = ?",
                    (nuevo, zona_id, empresa_id),
                )
                _registrar_traza(conn, "zona", zona_id, "cambiar_activo", str(zona["activo"]), str(nuevo), zona["nombre"], empresa_id)
        elif accion == "plantillas_zonas":
            creadas = _crear_zonas_base_pisos(conn, empresa_id)
            _registrar_traza(conn, "zona", None, "crear_zonas_base", "", str(creadas), "Plantillas base de zonas comunes", empresa_id)
            flash(f"Zonas base revisadas. Nuevas zonas creadas: {creadas}.", "success")
        conn.commit()
        conn.close()
        return redirect(url_for("pisos.configuracion"))
    config = _config_pisos(conn, empresa_id)
    zonas = conn.execute(
        "SELECT * FROM pisos_zonas WHERE empresa_id = ? ORDER BY activo DESC, bloque, planta, nombre",
        (empresa_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "pisos/configuracion.html",
        config=config,
        zonas=zonas,
        tipos_zona=TIPOS_ZONA,
        modos_lavanderia=MODOS_LAVANDERIA,
    )


@pisos_bp.route("/imprimir")
@login_required
@_require_pisos_gestion
def imprimir():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    fecha_txt = request.args.get("fecha") or date.today().isoformat()
    fecha_txt = (_parse_date(fecha_txt, date.today())).isoformat()
    estado = request.args.get("estado") or ""
    bloque = request.args.get("bloque") or ""
    planta = request.args.get("planta") or ""
    trabajador = request.args.get("trabajador") or ""
    _generar_partes_dia(conn, empresa_id, fecha_txt)
    conn.commit()
    trabajos = _partes_query(conn, empresa_id, fecha_txt, estado=estado, bloque=bloque, planta=planta, trabajador=trabajador)
    resumen = {
        "partes": {estado_key: sum(1 for t in trabajos if t["estado"] == estado_key) for estado_key in ESTADOS_PARTE},
        "lenceria": {
            "sabanas": sum(int(t["sabanas_previstas"] or 0) for t in trabajos),
            "toallas": sum(int(t["toallas_previstas"] or 0) for t in trabajos),
            "alfombrines": sum(int(t["alfombrines_previstos"] or 0) for t in trabajos),
        },
    }
    conn.close()
    return render_template(
        "pisos/parte_print.html",
        fecha=fecha_txt,
        trabajos=trabajos,
        resumen=resumen,
        filtros={"estado": estado, "bloque": bloque, "planta": planta, "trabajador": trabajador},
        tipos_cama=TIPOS_CAMA,
        tipos_aire=TIPOS_AIRE,
    )


@pisos_bp.route("/lavanderia", methods=["GET", "POST"])
@login_required
@_require_pisos_gestion
def lavanderia():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        accion = request.form.get("accion") or "crear_lavanderia"
        if accion == "crear_lavanderia":
            nombre = (request.form.get("nombre") or "").strip()
            tipo = request.form.get("tipo") or "externa"
            if tipo not in CIRCUITOS_LAVANDERIA:
                tipo = "externa"
            if nombre:
                cursor = conn.execute(
                    """
                    INSERT INTO pisos_lavanderias
                    (nombre, tipo, contacto, telefono, email, direccion, productos_lavado,
                     instrucciones, activo, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        nombre,
                        tipo,
                        request.form.get("contacto") or "",
                        request.form.get("telefono") or "",
                        request.form.get("email") or "",
                        request.form.get("direccion") or "",
                        request.form.get("productos_lavado") or "",
                        request.form.get("instrucciones") or "",
                        empresa_id,
                    ),
                )
                _registrar_traza(conn, "lavanderia", cursor.lastrowid, "crear_lavanderia", "", "activa", nombre, empresa_id)
                flash("Lavanderia registrada.", "success")
        elif accion == "toggle_lavanderia":
            lavanderia_id = _to_int(request.form.get("lavanderia_id"), 0)
            row = conn.execute(
                "SELECT * FROM pisos_lavanderias WHERE id = ? AND empresa_id = ?",
                (lavanderia_id, empresa_id),
            ).fetchone()
            if row:
                nuevo = 0 if row["activo"] else 1
                conn.execute(
                    "UPDATE pisos_lavanderias SET activo = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
                    (nuevo, lavanderia_id, empresa_id),
                )
                _registrar_traza(conn, "lavanderia", lavanderia_id, "cambiar_activo", str(row["activo"]), str(nuevo), row["nombre"], empresa_id)
        elif accion == "movimiento":
            movimiento_id = _to_int(request.form.get("movimiento_id"), 0)
            movimiento = conn.execute(
                "SELECT * FROM pisos_lenceria_movimientos WHERE id = ? AND empresa_id = ?",
                (movimiento_id, empresa_id),
            ).fetchone()
            if movimiento:
                estado = request.form.get("estado_lavanderia") or movimiento["estado_lavanderia"] or "pendiente"
                if estado not in ESTADOS_LAVANDERIA:
                    estado = "pendiente"
                circuito = request.form.get("circuito_lavanderia") or movimiento["circuito_lavanderia"] or "propia"
                if circuito not in CIRCUITOS_LAVANDERIA:
                    circuito = "propia"
                lavanderia_id = _to_int(request.form.get("lavanderia_proveedor_id"), 0)
                lavanderia_row = None
                if lavanderia_id:
                    lavanderia_row = conn.execute(
                        "SELECT * FROM pisos_lavanderias WHERE id = ? AND empresa_id = ?",
                        (lavanderia_id, empresa_id),
                    ).fetchone()
                conn.execute(
                    """
                    UPDATE pisos_lenceria_movimientos
                    SET circuito_lavanderia = ?, lavanderia_proveedor_id = ?, proveedor_lavanderia = ?,
                        estado_lavanderia = ?, fecha_salida = ?, fecha_retorno_prevista = ?,
                        fecha_retorno = ?, kg_estimados = ?, coste_estimado = ?, coste_real = ?,
                        albaran_lavanderia = ?, lote_retorno = ?, productos_lavado = ?,
                        observaciones = ?, validado_por = ?, validado_at = CASE WHEN ? = 'validada' THEN CURRENT_TIMESTAMP ELSE validado_at END
                    WHERE id = ? AND empresa_id = ?
                    """,
                    (
                        circuito,
                        lavanderia_id or None,
                        (lavanderia_row["nombre"] if lavanderia_row else request.form.get("proveedor_lavanderia")) or movimiento["proveedor_lavanderia"] or "",
                        estado,
                        request.form.get("fecha_salida") or movimiento["fecha_salida"] or "",
                        request.form.get("fecha_retorno_prevista") or movimiento["fecha_retorno_prevista"] or "",
                        request.form.get("fecha_retorno") or (date.today().isoformat() if estado in {"recibida", "validada"} and not movimiento["fecha_retorno"] else movimiento["fecha_retorno"]),
                        _to_float(request.form.get("kg_estimados"), movimiento["kg_estimados"] or 0),
                        _to_float(request.form.get("coste_estimado"), movimiento["coste_estimado"] or 0),
                        _to_float(request.form.get("coste_real"), movimiento["coste_real"] or 0),
                        request.form.get("albaran_lavanderia") or movimiento["albaran_lavanderia"] or "",
                        request.form.get("lote_retorno") or movimiento["lote_retorno"] or "",
                        request.form.get("productos_lavado") or movimiento["productos_lavado"] or "",
                        request.form.get("observaciones") or movimiento["observaciones"] or "",
                        _usuario_id() if estado == "validada" else movimiento["validado_por"],
                        estado,
                        movimiento_id,
                        empresa_id,
                    ),
                )
                _registrar_traza(conn, "lenceria", movimiento_id, "actualizar_lavanderia", movimiento["estado_lavanderia"] or "", estado, movimiento["lote_lavanderia"] or "", empresa_id)
                flash("Movimiento de lavanderia actualizado.", "success")
        conn.commit()
        conn.close()
        return redirect(url_for("pisos.lavanderia"))

    hoy = date.today().isoformat()
    desde = request.args.get("desde") or hoy
    hasta = request.args.get("hasta") or desde
    desde = (_parse_date(desde, date.today())).isoformat()
    hasta = (_parse_date(hasta, _parse_date(desde, date.today()))).isoformat()
    circuito = request.args.get("circuito") or ""
    estado = request.args.get("estado") or ""
    filtros = ["m.empresa_id = ?", "date(m.fecha) BETWEEN date(?) AND date(?)"]
    params = [empresa_id, desde, hasta]
    if circuito:
        filtros.append("m.circuito_lavanderia = ?")
        params.append(circuito)
    if estado:
        filtros.append("m.estado_lavanderia = ?")
        params.append(estado)
    movimientos = conn.execute(
        f"""
        SELECT m.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               r.codigo AS reserva_codigo, r.titular_nombre,
               l.nombre AS lavanderia_nombre, l.tipo AS lavanderia_tipo
        FROM pisos_lenceria_movimientos m
        LEFT JOIN habitaciones_recepcion h ON m.habitacion_id = h.id
        LEFT JOIN reservas_recepcion r ON m.reserva_id = r.id
        LEFT JOIN pisos_lavanderias l ON m.lavanderia_proveedor_id = l.id
        WHERE {" AND ".join(filtros)}
        ORDER BY date(m.fecha) DESC, m.id DESC
        """,
        params,
    ).fetchall()
    resumen = conn.execute(
        f"""
        SELECT COALESCE(m.circuito_lavanderia, 'propia') AS circuito,
               COALESCE(m.estado_lavanderia, 'pendiente') AS estado,
               SUM(COALESCE(m.sabanas, 0)) AS sabanas,
               SUM(COALESCE(m.toallas, 0)) AS toallas,
               SUM(COALESCE(m.alfombrines, 0)) AS alfombrines,
               SUM(COALESCE(m.coste_real, 0)) AS coste_real,
               COUNT(*) AS movimientos
        FROM pisos_lenceria_movimientos m
        WHERE {" AND ".join(filtros)}
        GROUP BY COALESCE(m.circuito_lavanderia, 'propia'), COALESCE(m.estado_lavanderia, 'pendiente')
        ORDER BY circuito, estado
        """,
        params,
    ).fetchall()
    lavanderias = _lavanderias_pisos(conn, empresa_id, solo_activas=False)
    config = _config_pisos(conn, empresa_id)
    if request.args.get("imprimir"):
        filas = []
        for movimiento in movimientos:
            origen = movimiento["habitacion_numero"] or movimiento["habitacion_nombre"] or "-"
            lavanderia_nombre = movimiento["lavanderia_nombre"] or movimiento["proveedor_lavanderia"] or "-"
            filas.append(
                [
                    movimiento["fecha"],
                    origen,
                    f"S{movimiento['sabanas'] or 0} T{movimiento['toallas'] or 0} A{movimiento['alfombrines'] or 0}",
                    CIRCUITOS_LAVANDERIA.get(movimiento["circuito_lavanderia"], movimiento["circuito_lavanderia"] or "propia"),
                    lavanderia_nombre,
                    ESTADOS_LAVANDERIA.get(movimiento["estado_lavanderia"], movimiento["estado_lavanderia"] or "pendiente"),
                    movimiento["albaran_lavanderia"] or "-",
                    movimiento["fecha_retorno"] or movimiento["fecha_retorno_prevista"] or "-",
                ]
            )
        resumen_print = [
            f"{CIRCUITOS_LAVANDERIA.get(row['circuito'], row['circuito'])} / {ESTADOS_LAVANDERIA.get(row['estado'], row['estado'])}: S{row['sabanas'] or 0} T{row['toallas'] or 0} A{row['alfombrines'] or 0}"
            for row in resumen
        ]
        conn.close()
        return _render_pisos_registro_print(
            "Control de lavanderia",
            {
                "Desde": desde,
                "Hasta": hasta,
                "Circuito": CIRCUITOS_LAVANDERIA.get(circuito, circuito or "Todos"),
                "Estado": ESTADOS_LAVANDERIA.get(estado, estado or "Todos"),
            },
            ["Fecha", "Origen", "Cantidades", "Circuito", "Lavanderia", "Estado", "Albaran", "Retorno"],
            filas,
            resumen_print,
        )
    conn.close()
    return render_template(
        "pisos/lavanderia.html",
        movimientos=movimientos,
        resumen=resumen,
        lavanderias=lavanderias,
        config=config,
        modos_lavanderia=MODOS_LAVANDERIA,
        circuitos=CIRCUITOS_LAVANDERIA,
        estados=ESTADOS_LAVANDERIA,
        filtros={"desde": desde, "hasta": hasta, "circuito": circuito, "estado": estado},
    )


@pisos_bp.route("/lenceria")
@login_required
@_require_pisos_gestion
def lenceria():
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    desde = request.args.get("desde") or date.today().isoformat()
    hasta = request.args.get("hasta") or desde
    desde = (_parse_date(desde, date.today())).isoformat()
    hasta = (_parse_date(hasta, _parse_date(desde, date.today()))).isoformat()
    movimientos = conn.execute(
        """
        SELECT m.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               r.codigo AS reserva_codigo, r.titular_nombre
        FROM pisos_lenceria_movimientos m
        LEFT JOIN habitaciones_recepcion h ON m.habitacion_id = h.id
        LEFT JOIN reservas_recepcion r ON m.reserva_id = r.id
        WHERE m.empresa_id = ? AND date(m.fecha) BETWEEN date(?) AND date(?)
        ORDER BY date(m.fecha) DESC, m.id DESC
        """,
        (empresa_id, desde, hasta),
    ).fetchall()
    resumen = conn.execute(
        """
        SELECT SUM(COALESCE(sabanas, 0)) AS sabanas,
               SUM(COALESCE(toallas, 0)) AS toallas,
               SUM(COALESCE(alfombrines, 0)) AS alfombrines
        FROM pisos_lenceria_movimientos
        WHERE empresa_id = ? AND date(fecha) BETWEEN date(?) AND date(?)
        """,
        (empresa_id, desde, hasta),
    ).fetchone()
    resumen_data = {"sabanas": resumen["sabanas"] or 0, "toallas": resumen["toallas"] or 0, "alfombrines": resumen["alfombrines"] or 0}
    if request.args.get("imprimir"):
        filas = []
        for movimiento in movimientos:
            filas.append(
                [
                    movimiento["fecha"],
                    movimiento["habitacion_numero"] or movimiento["habitacion_nombre"] or "-",
                    movimiento["reserva_codigo"] or movimiento["reserva_id"] or "-",
                    f"S{movimiento['sabanas'] or 0} T{movimiento['toallas'] or 0} A{movimiento['alfombrines'] or 0}",
                    movimiento["circuito_lavanderia"] or "-",
                    movimiento["estado_lavanderia"] or "-",
                    movimiento["proveedor_lavanderia"] or "-",
                    movimiento["lote_lavanderia"] or "-",
                ]
            )
        conn.close()
        return _render_pisos_registro_print(
            "Registro de lenceria",
            {"Desde": desde, "Hasta": hasta},
            ["Fecha", "Habitacion", "Reserva", "Cantidades", "Circuito", "Estado", "Lavanderia", "Lote"],
            filas,
            [f"Sabanas: {resumen_data['sabanas']}", f"Toallas: {resumen_data['toallas']}", f"Alfombrines: {resumen_data['alfombrines']}"],
        )
    conn.close()
    return render_template(
        "pisos/lenceria.html",
        movimientos=movimientos,
        resumen=resumen_data,
        filtros={"desde": desde, "hasta": hasta},
    )


@pisos_bp.route("/partes/<int:parte_id>/estado", methods=["POST"])
@login_required
@_require_pisos_gestion
def cambiar_estado_parte(parte_id):
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    parte = conn.execute(
        """
        SELECT p.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               h.estado AS habitacion_estado
        FROM pisos_partes_limpieza p
        JOIN habitaciones_recepcion h ON p.habitacion_id = h.id
        WHERE p.id = ? AND p.empresa_id = ?
        """,
        (parte_id, empresa_id),
    ).fetchone()
    if not parte:
        conn.close()
        abort(404)
    estado = request.form.get("estado") or "pendiente"
    if estado not in ESTADOS_PARTE:
        estado = "pendiente"
    resultado = request.form.get("resultado") or parte["resultado"] or ""
    observaciones = request.form.get("observaciones") or parte["observaciones"] or ""
    asignado_a = request.form.get("asignado_a") or parte["asignado_a"] or ""
    productos_usados = request.form.get("productos_usados") or parte["productos_usados"] or ""
    iniciado_at = parte["iniciado_at"]
    cerrado_at = parte["cerrado_at"]
    if estado == "en_curso" and not iniciado_at:
        iniciado_at = datetime.now().isoformat(timespec="seconds")
    if estado in {"hecha", "incidencia", "cancelada"} and not cerrado_at:
        cerrado_at = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE pisos_partes_limpieza
        SET estado = ?, resultado = ?, observaciones = ?, asignado_a = ?, productos_usados = ?,
            iniciado_at = ?, cerrado_at = ?, cerrado_por = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (
            estado,
            resultado,
            observaciones,
            asignado_a,
            productos_usados,
            iniciado_at,
            cerrado_at,
            _usuario_id(),
            parte_id,
            empresa_id,
        ),
    )
    habitacion_estado_nuevo = None
    if estado == "hecha":
        nuevo_estado = request.form.get("habitacion_estado") or "disponible"
        if parte["tipo"] == "estancia":
            nuevo_estado = "ocupada"
        if nuevo_estado not in ESTADOS_HABITACION:
            nuevo_estado = "disponible"
        conn.execute(
            "UPDATE habitaciones_recepcion SET estado = ? WHERE id = ? AND empresa_id = ?",
            (nuevo_estado, parte["habitacion_id"], empresa_id),
        )
        habitacion_estado_nuevo = nuevo_estado
        if _form_bool("registrar_appcc", default=True):
            appcc_registro_id = _registrar_appcc_pisos(conn, parte, productos_usados, resultado, empresa_id)
            conn.execute(
                """
                UPDATE pisos_partes_limpieza
                SET appcc_registro_id = ?
                WHERE id = ? AND empresa_id = ?
                """,
                (appcc_registro_id, parte_id, empresa_id),
            )
    elif estado == "en_curso":
        conn.execute(
            "UPDATE habitaciones_recepcion SET estado = 'limpieza' WHERE id = ? AND empresa_id = ?",
            (parte["habitacion_id"], empresa_id),
        )
        habitacion_estado_nuevo = "limpieza"
    elif estado == "incidencia":
        conn.execute(
            "UPDATE habitaciones_recepcion SET estado = 'mantenimiento' WHERE id = ? AND empresa_id = ?",
            (parte["habitacion_id"], empresa_id),
        )
        habitacion_estado_nuevo = "mantenimiento"
        incidencia_id = _crear_incidencia_pisos(conn, parte, observaciones or resultado, empresa_id)
        orden_id = _crear_ot_mantenimiento_desde_parte(conn, parte, incidencia_id, observaciones or resultado, empresa_id)
        conn.execute(
            """
            UPDATE pisos_partes_limpieza
            SET incidencia_id = ?, mantenimiento_orden_id = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (incidencia_id, orden_id, parte_id, empresa_id),
        )
    _registrar_traza(
        conn,
        "parte",
        parte_id,
        "cambiar_estado",
        parte["estado"] or "",
        estado,
        f"Asignado: {asignado_a or '-'}; resultado: {resultado or '-'}; productos: {productos_usados or '-'}",
        empresa_id,
    )
    if habitacion_estado_nuevo and (parte["habitacion_estado"] or "") != habitacion_estado_nuevo:
        _registrar_traza(
            conn,
            "habitacion",
            parte["habitacion_id"],
            "cambiar_estado",
            parte["habitacion_estado"] or "",
            habitacion_estado_nuevo,
            f"Por parte #{parte_id} ({TIPOS_PARTE.get(parte['tipo'], parte['tipo'])})",
            empresa_id,
        )
    conn.commit()
    fecha_txt = parte["fecha"]
    conn.close()
    flash("Parte actualizado.", "success")
    return redirect(request.referrer or url_for("pisos.dashboard", fecha=fecha_txt))


@pisos_bp.route("/habitaciones/<int:habitacion_id>/estado", methods=["POST"])
@login_required
@_require_pisos_gestion
def cambiar_estado_habitacion(habitacion_id):
    conn = get_db()
    _ensure_pisos_schema(conn)
    empresa_id = _empresa_id()
    estado = request.form.get("estado") or "disponible"
    if estado not in ESTADOS_HABITACION:
        estado = "disponible"
    row = conn.execute(
        "SELECT id, estado, numero, nombre FROM habitaciones_recepcion WHERE id = ? AND empresa_id = ?",
        (habitacion_id, empresa_id),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE habitaciones_recepcion SET estado = ? WHERE id = ? AND empresa_id = ?",
        (estado, habitacion_id, empresa_id),
    )
    _registrar_traza(
        conn,
        "habitacion",
        habitacion_id,
        "cambiar_estado",
        row["estado"] or "",
        estado,
        f"Habitacion {row['numero'] or row['nombre'] or habitacion_id}",
        empresa_id,
    )
    if estado in {"limpieza", "repaso", "mantenimiento"}:
        fecha_txt = request.form.get("fecha") or date.today().isoformat()
        fecha_txt = (_parse_date(fecha_txt, date.today())).isoformat()
        tipo = "mantenimiento" if estado == "mantenimiento" else ("salida" if estado == "limpieza" else "repaso")
        _crear_parte_si_falta(
            conn,
            empresa_id,
            fecha_txt,
            habitacion_id,
            tipo,
            None,
            "alta" if tipo != "repaso" else "media",
            request.form.get("observaciones") or f"Marcada como {estado}",
        )
    conn.commit()
    conn.close()
    flash("Estado de habitacion actualizado.", "success")
    return redirect(request.referrer or url_for("pisos.habitaciones"))

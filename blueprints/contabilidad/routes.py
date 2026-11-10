from datetime import datetime, timedelta
import calendar

from flask import flash, redirect, render_template, request, session, url_for

from blueprints.auth.routes import (
    login_required,
    usuario_departamento_id,
    usuario_es_global_operacion,
    usuario_subdepartamento_id,
    usuario_tiene_permiso,
)
from database import get_db
from helpers.centros_helper import (
    asignar_centro_desde_departamento,
    centro_activo_id,
    ids_departamentos_centro,
    ids_puntos_venta_centro,
)
from . import contabilidad_bp


CUENTAS_BASE = [
    ("400", "Proveedores", "pasivo", "4", "Deudas con proveedores por compras."),
    ("410", "Acreedores por prestaciones de servicios", "pasivo", "4", "Servicios exteriores y acreedores varios."),
    ("430", "Clientes", "activo", "4", "Clientes y facturas de venta pendientes."),
    ("465", "Remuneraciones pendientes de pago", "pasivo", "4", "Nominas y liquidaciones pendientes."),
    ("472", "IVA soportado", "activo", "4", "Impuesto soportado en compras."),
    ("477", "IVA repercutido", "pasivo", "4", "Impuesto repercutido en ventas."),
    ("570", "Caja", "tesoreria", "5", "Caja de efectivo."),
    ("572", "Bancos", "tesoreria", "5", "Cuentas bancarias."),
    ("600", "Compras de mercaderias", "gasto", "6", "Alimentos, bebidas y mercaderias."),
    ("602", "Compras de otros aprovisionamientos", "gasto", "6", "Limpieza, menaje, material y otros consumibles."),
    ("621", "Arrendamientos y canones", "gasto", "6", "Alquileres y canones."),
    ("622", "Reparaciones y conservacion", "gasto", "6", "Mantenimiento externo y reparaciones."),
    ("623", "Servicios profesionales independientes", "gasto", "6", "Asesoria, consultoria y servicios profesionales."),
    ("624", "Transportes", "gasto", "6", "Transportes y portes."),
    ("625", "Primas de seguros", "gasto", "6", "Seguros."),
    ("626", "Servicios bancarios y similares", "gasto", "6", "Comisiones bancarias."),
    ("627", "Publicidad, propaganda y relaciones publicas", "gasto", "6", "Marketing y plataformas comerciales."),
    ("628", "Suministros", "gasto", "6", "Luz, agua, gas y telecomunicaciones."),
    ("629", "Otros servicios", "gasto", "6", "Otros gastos de explotacion."),
    ("640", "Sueldos y salarios", "gasto", "6", "Coste salarial bruto."),
    ("642", "Seguridad Social a cargo de la empresa", "gasto", "6", "Coste de seguridad social."),
    ("700", "Ventas", "ingreso", "7", "Ventas de productos."),
    ("705", "Prestaciones de servicios", "ingreso", "7", "Alojamiento, restauracion y servicios hoteleros."),
]


TIPOS_DOCUMENTO = [
    ("factura_compra", "Factura compra"),
    ("albaran", "Albaran"),
    ("ticket", "Ticket"),
    ("ticket_venta", "Ticket venta"),
    ("gasto", "Gasto"),
    ("nomina", "Nomina"),
    ("impuesto", "Impuesto"),
    ("comision", "Comision plataforma"),
    ("factura_venta", "Factura venta"),
    ("recibo", "Recibo"),
]


ESTADOS_DOCUMENTO = [
    ("pendiente_validar", "Pendiente validar"),
    ("pendiente_factura", "Pendiente factura"),
    ("pendiente_pago", "Pendiente pago"),
    ("pagado_parcial", "Pagado parcial"),
    ("pagado", "Pagado"),
    ("facturado", "Facturado"),
    ("vencido", "Vencido"),
    ("bloqueado", "Bloqueado"),
    ("cancelado", "Cancelado"),
]


PERMISOS_CONTROL_ECONOMICO = {
    "contabilidad_ver",
    "contabilidad_gestionar",
}


@contabilidad_bp.before_request
def _bloquear_control_economico_sin_permiso():
    if "user_id" not in session:
        return None
    if any(usuario_tiene_permiso(permiso) for permiso in PERMISOS_CONTROL_ECONOMICO):
        return None
    flash("No tienes permisos para acceder al control economico.", "warning")
    return redirect(url_for("home"))


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _columns(conn, table_name):
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
    except Exception:
        return []


def _add_column(conn, table_name, column_sql):
    name = column_sql.strip().split()[0]
    if _table_exists(conn, table_name) and name not in _columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def _to_float(value, default=0.0):
    try:
        txt = str(value if value is not None else "").strip().replace(",", ".")
        return float(txt) if txt else default
    except Exception:
        return default


def _to_int_or_none(value):
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def _parse_date_text(value):
    try:
        return datetime.strptime((value or "").strip()[:10], "%Y-%m-%d")
    except Exception:
        return datetime.now()


def _add_months(fecha, meses):
    year = fecha.year + ((fecha.month - 1 + meses) // 12)
    month = ((fecha.month - 1 + meses) % 12) + 1
    dias_mes = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day = min(fecha.day, dias_mes[month - 1])
    return fecha.replace(year=year, month=month, day=day)


def _siguiente_fecha_recurrente(fecha_txt, frecuencia):
    fecha = _parse_date_text(fecha_txt)
    frecuencia = (frecuencia or "mensual").strip().lower()
    if frecuencia == "semanal":
        return (fecha + timedelta(days=7)).strftime("%Y-%m-%d")
    if frecuencia == "trimestral":
        return _add_months(fecha, 3).strftime("%Y-%m-%d")
    if frecuencia == "semestral":
        return _add_months(fecha, 6).strftime("%Y-%m-%d")
    if frecuencia == "anual":
        return _add_months(fecha, 12).strftime("%Y-%m-%d")
    return _add_months(fecha, 1).strftime("%Y-%m-%d")


def _ensure_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contabilidad_cuentas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT NOT NULL,
            nombre TEXT NOT NULL,
            tipo TEXT,
            grupo TEXT,
            descripcion TEXT,
            activa INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            UNIQUE(codigo, empresa_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contabilidad_terceros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT DEFAULT 'proveedor',
            nombre TEXT NOT NULL,
            nif TEXT,
            email TEXT,
            telefono TEXT,
            codigo_contable TEXT,
            proveedor_id INTEGER,
            cliente_id INTEGER,
            empleado_id INTEGER,
            canal_id INTEGER,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contabilidad_documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            numero TEXT,
            tercero_id INTEGER,
            proveedor_id INTEGER,
            fecha_documento DATE,
            fecha_vencimiento DATE,
            estado TEXT DEFAULT 'pendiente_pago',
            base_imponible REAL DEFAULT 0,
            impuesto_importe REAL DEFAULT 0,
            total REAL DEFAULT 0,
            pagado REAL DEFAULT 0,
            pendiente REAL DEFAULT 0,
            moneda TEXT DEFAULT 'EUR',
            cuenta_contable TEXT,
            centro_coste TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            reserva_id INTEGER,
            empleado_id INTEGER,
            origen_modulo TEXT,
            origen_tipo TEXT,
            origen_id INTEGER,
            archivo TEXT,
            observaciones TEXT,
            creado_por TEXT,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            empresa_id INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contabilidad_lineas_documento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento_id INTEGER NOT NULL,
            concepto TEXT NOT NULL,
            cantidad REAL DEFAULT 1,
            precio_unitario REAL DEFAULT 0,
            base REAL DEFAULT 0,
            impuesto_tipo REAL DEFAULT 0,
            impuesto_importe REAL DEFAULT 0,
            total REAL DEFAULT 0,
            cuenta_contable TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (documento_id) REFERENCES contabilidad_documentos(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contabilidad_pagos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento_id INTEGER NOT NULL,
            fecha_pago DATE NOT NULL,
            metodo TEXT DEFAULT 'transferencia',
            importe REAL NOT NULL,
            cuenta_tesoreria TEXT,
            referencia TEXT,
            observaciones TEXT,
            usuario TEXT,
            fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (documento_id) REFERENCES contabilidad_documentos(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contabilidad_impuestos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo TEXT NOT NULL,
            porcentaje REAL NOT NULL,
            territorio TEXT DEFAULT 'ES',
            fecha_inicio DATE,
            fecha_fin DATE,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contabilidad_factura_albaranes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factura_id INTEGER NOT NULL,
            albaran_id INTEGER NOT NULL,
            recepcion_id INTEGER,
            importe_albaran REAL DEFAULT 0,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(factura_id, albaran_id, empresa_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contabilidad_recurrentes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo_documento TEXT DEFAULT 'gasto',
            tercero_id INTEGER,
            proveedor_id INTEGER,
            cuenta_contable TEXT,
            base_imponible REAL DEFAULT 0,
            impuesto_pct REAL DEFAULT 0,
            impuesto_importe REAL DEFAULT 0,
            total REAL DEFAULT 0,
            frecuencia TEXT DEFAULT 'mensual',
            dia_mes INTEGER DEFAULT 1,
            siguiente_fecha DATE,
            centro_coste TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            observaciones TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contabilidad_presupuestos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            anio INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            estado TEXT DEFAULT 'borrador',
            origen TEXT DEFAULT 'manual',
            observaciones TEXT,
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
            actualizado_en DATETIME,
            UNIQUE(empresa_id, centro_id, anio, nombre)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contabilidad_presupuesto_lineas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            presupuesto_id INTEGER NOT NULL,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            anio INTEGER NOT NULL,
            mes INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            bloque TEXT NOT NULL,
            concepto TEXT NOT NULL,
            importe REAL DEFAULT 0,
            cuenta_contable TEXT,
            fuente TEXT,
            fiabilidad TEXT DEFAULT 'estimado',
            observaciones TEXT,
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (presupuesto_id) REFERENCES contabilidad_presupuestos(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_conta_presupuesto_lineas
        ON contabilidad_presupuesto_lineas(empresa_id, centro_id, anio, mes, tipo, bloque)
    """)

    for table_name in (
        "contabilidad_cuentas", "contabilidad_terceros", "contabilidad_documentos",
        "contabilidad_lineas_documento", "contabilidad_pagos", "contabilidad_impuestos",
        "contabilidad_factura_albaranes", "contabilidad_recurrentes",
        "contabilidad_presupuestos", "contabilidad_presupuesto_lineas",
    ):
        _add_column(conn, table_name, "empresa_id INTEGER DEFAULT 1")
    _add_column(conn, "contabilidad_presupuestos", "centro_id INTEGER")
    _add_column(conn, "contabilidad_presupuesto_lineas", "centro_id INTEGER")
    _add_column(conn, "contabilidad_documentos", "centro_id INTEGER")
    _add_column(conn, "contabilidad_recurrentes", "centro_id INTEGER")
    _add_column(conn, "contabilidad_terceros", "centro_id INTEGER")

    if _table_exists(conn, "recepciones"):
        for col in [
            "documento_economico_tipo TEXT",
            "estado_facturacion TEXT",
            "fecha_vencimiento_economica DATE",
            "forma_pago_economica TEXT",
            "contabilidad_documento_id INTEGER",
            "contabilidad_factura_id INTEGER",
        ]:
            _add_column(conn, "recepciones", col)
    if _table_exists(conn, "proveedores"):
        for col in [
            "documento_habitual TEXT DEFAULT 'albaran'",
            "facturacion_habitual TEXT DEFAULT 'mensual'",
            "forma_pago TEXT DEFAULT 'transferencia'",
            "dias_pago INTEGER DEFAULT 30",
        ]:
            _add_column(conn, "proveedores", col)

    existentes = conn.execute(
        "SELECT COUNT(*) AS total FROM contabilidad_cuentas WHERE empresa_id = ?",
        (session.get('user_empresa_id', 1),),
    ).fetchone()["total"]
    if not existentes:
        for codigo, nombre, tipo, grupo, descripcion in CUENTAS_BASE:
            conn.execute("""
                INSERT OR IGNORE INTO contabilidad_cuentas
                (codigo, nombre, tipo, grupo, descripcion, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (codigo, nombre, tipo, grupo, descripcion, session.get('user_empresa_id', 1)))

    impuestos = conn.execute(
        "SELECT COUNT(*) AS total FROM contabilidad_impuestos WHERE empresa_id = ?",
        (session.get('user_empresa_id', 1),),
    ).fetchone()["total"]
    if not impuestos:
        for nombre, tipo, porcentaje, territorio in [
            ("IVA general", "IVA", 21, "ES"),
            ("IVA reducido hosteleria", "IVA", 10, "ES"),
            ("IVA superreducido", "IVA", 4, "ES"),
            ("Exento", "EXENTO", 0, "ES"),
            ("IGIC general", "IGIC", 7, "IC"),
        ]:
            conn.execute("""
                INSERT INTO contabilidad_impuestos
                (nombre, tipo, porcentaje, territorio, activo, empresa_id)
                VALUES (?, ?, ?, ?, 1, ?)
            """, (nombre, tipo, porcentaje, territorio, session.get('user_empresa_id', 1)))
    _normalizar_centros_contabilidad(conn)


def _normalizar_centros_contabilidad(conn):
    if "centro_id" not in _columns(conn, "contabilidad_documentos"):
        return
    if _table_exists(conn, "departamentos") and "centro_id" in _columns(conn, "departamentos"):
        conn.execute("""
            UPDATE contabilidad_documentos
            SET centro_id = (
                SELECT d.centro_id FROM departamentos d
                WHERE d.id = contabilidad_documentos.departamento_id
                LIMIT 1
            )
            WHERE centro_id IS NULL AND departamento_id IS NOT NULL
        """)
    if _table_exists(conn, "puntos_venta") and "centro_id" in _columns(conn, "puntos_venta"):
        conn.execute("""
            UPDATE contabilidad_documentos
            SET centro_id = (
                SELECT pv.centro_id FROM puntos_venta pv
                WHERE pv.id = contabilidad_documentos.punto_venta_id
                LIMIT 1
            )
            WHERE centro_id IS NULL AND punto_venta_id IS NOT NULL
        """)
    conn.execute("""
        UPDATE contabilidad_documentos
        SET centro_id = 1
        WHERE empresa_id = 1 AND centro_id IS NULL
    """)
    if _table_exists(conn, "departamentos") and "centro_id" in _columns(conn, "departamentos"):
        conn.execute("""
            UPDATE contabilidad_recurrentes
            SET centro_id = (
                SELECT d.centro_id FROM departamentos d
                WHERE d.id = contabilidad_recurrentes.departamento_id
                LIMIT 1
            )
            WHERE centro_id IS NULL AND departamento_id IS NOT NULL
        """)
    if _table_exists(conn, "puntos_venta") and "centro_id" in _columns(conn, "puntos_venta"):
        conn.execute("""
            UPDATE contabilidad_recurrentes
            SET centro_id = (
                SELECT pv.centro_id FROM puntos_venta pv
                WHERE pv.id = contabilidad_recurrentes.punto_venta_id
                LIMIT 1
            )
            WHERE centro_id IS NULL AND punto_venta_id IS NOT NULL
        """)
    conn.execute("""
        UPDATE contabilidad_recurrentes
        SET centro_id = 1
        WHERE empresa_id = 1 AND centro_id IS NULL
    """)


def _centro_desde_departamento_o_pv(conn, departamento_id=None, punto_venta_id=None):
    centro_id = asignar_centro_desde_departamento(conn, departamento_id)
    if centro_id:
        return centro_id
    if punto_venta_id and _table_exists(conn, "puntos_venta") and "centro_id" in _columns(conn, "puntos_venta"):
        row = conn.execute("SELECT centro_id FROM puntos_venta WHERE id = ?", (punto_venta_id,)).fetchone()
        if row and row["centro_id"]:
            return row["centro_id"]
    return centro_activo_id()


def _ids_departamentos_contabilidad(conn, empresa_id, centro_id=None):
    centro_id = centro_id if centro_id is not None else centro_activo_id()
    if usuario_es_global_operacion():
        return ids_departamentos_centro(conn, empresa_id, centro_id) if centro_id else []
    departamento_id = usuario_departamento_id()
    if not departamento_id:
        return []
    if centro_id and _table_exists(conn, "departamentos") and "centro_id" in _columns(conn, "departamentos"):
        row = conn.execute(
            """
            SELECT id
            FROM departamentos
            WHERE id = ? AND empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1
            """,
            (departamento_id, empresa_id, centro_id),
        ).fetchone()
        return [departamento_id] if row else []
    return [departamento_id]


def _ids_subdepartamentos_contabilidad(conn, empresa_id, centro_id=None, dep_ids=None):
    if usuario_es_global_operacion():
        return []
    subdepartamento_id = usuario_subdepartamento_id()
    if not subdepartamento_id or not _table_exists(conn, "subdepartamentos"):
        return []
    dep_ids = dep_ids or _ids_departamentos_contabilidad(conn, empresa_id, centro_id)
    if not dep_ids:
        return []
    centro_id = centro_id if centro_id is not None else centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro_id and "centro_id" in _columns(conn, "subdepartamentos") else ""
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


def _ids_puntos_venta_contabilidad(conn, empresa_id, centro_id=None, dep_ids=None, subdep_ids=None):
    if not _table_exists(conn, "puntos_venta") or "departamento_id" not in _columns(conn, "puntos_venta"):
        return []
    centro_id = centro_id if centro_id is not None else centro_activo_id()
    if usuario_es_global_operacion():
        return ids_puntos_venta_centro(conn, empresa_id, centro_id) if centro_id else []
    dep_ids = dep_ids or _ids_departamentos_contabilidad(conn, empresa_id, centro_id)
    subdep_ids = subdep_ids or _ids_subdepartamentos_contabilidad(conn, empresa_id, centro_id, dep_ids)
    if not dep_ids:
        return []
    condiciones = [f"departamento_id IN ({','.join('?' for _ in dep_ids)})"]
    params = [empresa_id] + dep_ids
    if subdep_ids and "subdepartamento_id" in _columns(conn, "puntos_venta"):
        condiciones.append(f"subdepartamento_id IN ({','.join('?' for _ in subdep_ids)})")
        params.extend(subdep_ids)
    if centro_id and "centro_id" in _columns(conn, "puntos_venta"):
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


def _scope_contabilidad_documentos(alias, conn, empresa_id):
    centro_id = centro_activo_id()
    if not centro_id:
        return f"{alias}.empresa_id = ?", [empresa_id]
    condiciones = []
    params = []
    if usuario_es_global_operacion() and "centro_id" in _columns(conn, "contabilidad_documentos"):
        condiciones.append(f"{alias}.centro_id = ?")
        params.append(centro_id)
    dep_ids = _ids_departamentos_contabilidad(conn, empresa_id, centro_id)
    subdep_ids = _ids_subdepartamentos_contabilidad(conn, empresa_id, centro_id, dep_ids)
    if subdep_ids and "subdepartamento_id" in _columns(conn, "contabilidad_documentos"):
        condiciones.append(f"{alias}.subdepartamento_id IN ({','.join('?' for _ in subdep_ids)})")
        params.extend(subdep_ids)
    if dep_ids and "departamento_id" in _columns(conn, "contabilidad_documentos"):
        condiciones.append(f"{alias}.departamento_id IN ({','.join('?' for _ in dep_ids)})")
        params.extend(dep_ids)
    pv_ids = _ids_puntos_venta_contabilidad(conn, empresa_id, centro_id, dep_ids, subdep_ids)
    if pv_ids and "punto_venta_id" in _columns(conn, "contabilidad_documentos"):
        condiciones.append(f"{alias}.punto_venta_id IN ({','.join('?' for _ in pv_ids)})")
        params.extend(pv_ids)
    if not condiciones:
        return f"{alias}.empresa_id = ? AND 1 = 0", [empresa_id]
    return f"{alias}.empresa_id = ? AND ({' OR '.join(condiciones)})", [empresa_id] + params


def _scope_contabilidad_recurrentes(alias, conn, empresa_id):
    centro_id = centro_activo_id()
    if not centro_id:
        return f"{alias}.empresa_id = ?", [empresa_id]
    condiciones = []
    params = []
    if usuario_es_global_operacion() and "centro_id" in _columns(conn, "contabilidad_recurrentes"):
        condiciones.append(f"{alias}.centro_id = ?")
        params.append(centro_id)
    dep_ids = _ids_departamentos_contabilidad(conn, empresa_id, centro_id)
    subdep_ids = _ids_subdepartamentos_contabilidad(conn, empresa_id, centro_id, dep_ids)
    if subdep_ids and "subdepartamento_id" in _columns(conn, "contabilidad_recurrentes"):
        condiciones.append(f"{alias}.subdepartamento_id IN ({','.join('?' for _ in subdep_ids)})")
        params.extend(subdep_ids)
    if dep_ids and "departamento_id" in _columns(conn, "contabilidad_recurrentes"):
        condiciones.append(f"{alias}.departamento_id IN ({','.join('?' for _ in dep_ids)})")
        params.extend(dep_ids)
    pv_ids = _ids_puntos_venta_contabilidad(conn, empresa_id, centro_id, dep_ids, subdep_ids)
    if pv_ids and "punto_venta_id" in _columns(conn, "contabilidad_recurrentes"):
        condiciones.append(f"{alias}.punto_venta_id IN ({','.join('?' for _ in pv_ids)})")
        params.extend(pv_ids)
    if not condiciones:
        return f"{alias}.empresa_id = ? AND 1 = 0", [empresa_id]
    return f"{alias}.empresa_id = ? AND ({' OR '.join(condiciones)})", [empresa_id] + params


def _actualizar_estado_documento(conn, documento_id, empresa_id):
    row = conn.execute("""
        SELECT COALESCE(total, 0) AS total,
               COALESCE((SELECT SUM(importe) FROM contabilidad_pagos WHERE documento_id = contabilidad_documentos.id), 0) AS pagado,
               fecha_vencimiento
        FROM contabilidad_documentos
        WHERE id = ? AND empresa_id = ?
    """, (documento_id, empresa_id)).fetchone()
    if not row:
        return
    total = _to_float(row["total"], 0.0)
    pagado = min(_to_float(row["pagado"], 0.0), total)
    pendiente = max(total - pagado, 0.0)
    estado = "pagado" if pendiente <= 0.009 else "pagado_parcial" if pagado > 0 else "pendiente_pago"
    if estado == "pendiente_pago" and row["fecha_vencimiento"] and str(row["fecha_vencimiento"]) < datetime.now().strftime("%Y-%m-%d"):
        estado = "vencido"
    conn.execute("""
        UPDATE contabilidad_documentos
        SET pagado = ?, pendiente = ?, estado = ?
        WHERE id = ? AND empresa_id = ?
    """, (round(pagado, 2), round(pendiente, 2), estado, documento_id, empresa_id))


def _ensure_tercero_proveedor(conn, proveedor_id, empresa_id):
    if not proveedor_id:
        return None
    tercero = conn.execute("""
        SELECT id FROM contabilidad_terceros
        WHERE proveedor_id = ? AND empresa_id = ?
    """, (proveedor_id, empresa_id)).fetchone()
    if tercero:
        return tercero["id"]
    proveedor = conn.execute("""
        SELECT * FROM proveedores WHERE id = ? AND empresa_id = ?
    """, (proveedor_id, empresa_id)).fetchone()
    if not proveedor:
        return None
    codigo = f"400{int(proveedor_id):04d}"
    cur = conn.execute("""
        INSERT INTO contabilidad_terceros
        (tipo, nombre, nif, email, telefono, codigo_contable, proveedor_id, empresa_id)
        VALUES ('proveedor', ?, ?, ?, ?, ?, ?, ?)
    """, (
        proveedor["nombre"],
        proveedor["nif"] if "nif" in proveedor.keys() else None,
        proveedor["email"] if "email" in proveedor.keys() else None,
        proveedor["telefono"] if "telefono" in proveedor.keys() else None,
        codigo,
        proveedor_id,
        empresa_id,
    ))
    return cur.lastrowid


def _ensure_tercero_cliente(conn, nombre, nif, email, empresa_id, cliente_id=None, codigo_base="430"):
    nombre = (nombre or "").strip() or "Cliente contado"
    nif = (nif or "").strip()
    email = (email or "").strip()
    if cliente_id:
        tercero = conn.execute("""
            SELECT id FROM contabilidad_terceros
            WHERE cliente_id = ? AND empresa_id = ?
        """, (cliente_id, empresa_id)).fetchone()
        if tercero:
            return tercero["id"]
    if nif:
        tercero = conn.execute("""
            SELECT id FROM contabilidad_terceros
            WHERE UPPER(COALESCE(nif, '')) = UPPER(?) AND empresa_id = ?
              AND tipo IN ('cliente', 'cliente_tpv')
            ORDER BY id
            LIMIT 1
        """, (nif, empresa_id)).fetchone()
        if tercero:
            return tercero["id"]
    tercero = conn.execute("""
        SELECT id FROM contabilidad_terceros
        WHERE LOWER(nombre) = LOWER(?) AND empresa_id = ?
          AND tipo IN ('cliente', 'cliente_tpv')
        ORDER BY id
        LIMIT 1
    """, (nombre, empresa_id)).fetchone()
    if tercero:
        return tercero["id"]
    codigo = f"{codigo_base}{int(cliente_id or 0):04d}" if cliente_id else codigo_base
    cur = conn.execute("""
        INSERT INTO contabilidad_terceros
        (tipo, nombre, nif, email, codigo_contable, cliente_id, empresa_id)
        VALUES ('cliente', ?, ?, ?, ?, ?, ?)
    """, (nombre, nif or None, email or None, codigo, cliente_id, empresa_id))
    return cur.lastrowid


def _estado_documento_venta(estado_origen, total, pagado, saldo):
    estado_origen = (estado_origen or "").strip().lower()
    total = _to_float(total, 0.0)
    pagado = _to_float(pagado, 0.0)
    saldo = _to_float(saldo, max(total - pagado, 0.0))
    if estado_origen in {"anulada", "cancelada"}:
        return "cancelado"
    if total > 0 and saldo <= 0.009:
        return "pagado"
    if pagado > 0:
        return "pagado_parcial"
    return "pendiente_pago"


def _cuenta_tesoreria(metodo):
    metodo = (metodo or "").strip().lower()
    if metodo in {"efectivo", "caja"}:
        return "570"
    return "572"


def _opciones_base(conn, empresa_id):
    centro_id = centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro_id else ""
    centro_params = [centro_id] if centro_id else []
    dep_ids = _ids_departamentos_contabilidad(conn, empresa_id, centro_id)
    subdep_ids = _ids_subdepartamentos_contabilidad(conn, empresa_id, centro_id, dep_ids)
    pv_ids = _ids_puntos_venta_contabilidad(conn, empresa_id, centro_id, dep_ids, subdep_ids)
    dep_sql = ""
    dep_params = []
    subdep_sql = ""
    subdep_params = []
    pv_sql = ""
    pv_params = []
    if not usuario_es_global_operacion():
        if dep_ids:
            dep_sql = f" AND id IN ({','.join('?' for _ in dep_ids)})"
            dep_params = dep_ids
        else:
            dep_sql = " AND 1 = 0"
        if subdep_ids:
            subdep_sql = f" AND id IN ({','.join('?' for _ in subdep_ids)})"
            subdep_params = subdep_ids
        elif dep_ids:
            subdep_sql = f" AND departamento_id IN ({','.join('?' for _ in dep_ids)})"
            subdep_params = dep_ids
        else:
            subdep_sql = " AND 1 = 0"
        if pv_ids:
            pv_sql = f" AND id IN ({','.join('?' for _ in pv_ids)})"
            pv_params = pv_ids
        else:
            pv_sql = " AND 1 = 0"
    return {
        "terceros": conn.execute(f"""
            SELECT id, nombre, tipo, codigo_contable
            FROM contabilidad_terceros
            WHERE empresa_id = ? AND activo = 1
              {centro_sql}
            ORDER BY nombre
        """, [empresa_id] + centro_params).fetchall(),
        "proveedores": conn.execute("""
            SELECT id, nombre FROM proveedores
            WHERE empresa_id = ? AND activo = 1
            ORDER BY nombre
        """, (empresa_id,)).fetchall() if _table_exists(conn, "proveedores") else [],
        "cuentas": conn.execute("""
            SELECT codigo, nombre, tipo
            FROM contabilidad_cuentas
            WHERE empresa_id = ? AND activa = 1
            ORDER BY codigo
        """, (empresa_id,)).fetchall(),
        "departamentos": conn.execute(f"""
            SELECT id, nombre FROM departamentos
            WHERE empresa_id = ? AND activo = 1
              {centro_sql}
              {dep_sql}
            ORDER BY nombre
        """, [empresa_id] + centro_params + dep_params).fetchall() if _table_exists(conn, "departamentos") else [],
        "subdepartamentos": conn.execute(f"""
            SELECT id, nombre FROM subdepartamentos
            WHERE empresa_id = ? AND activo = 1
              {centro_sql}
              {subdep_sql}
            ORDER BY nombre
        """, [empresa_id] + centro_params + subdep_params).fetchall() if _table_exists(conn, "subdepartamentos") else [],
        "puntos_venta": conn.execute(f"""
            SELECT id, nombre FROM puntos_venta
            WHERE empresa_id = ? AND activo = 1
              {centro_sql}
              {pv_sql}
            ORDER BY nombre
        """, [empresa_id] + centro_params + pv_params).fetchall() if _table_exists(conn, "puntos_venta") else [],
    }


def _presupuesto_get_or_create(conn, empresa_id, centro_id, anio):
    nombre = f"Presupuesto operativo {anio}"
    row = conn.execute(
        """
        SELECT id FROM contabilidad_presupuestos
        WHERE empresa_id=? AND COALESCE(centro_id,0)=COALESCE(?,0) AND anio=? AND nombre=?
        """,
        (empresa_id, centro_id, anio, nombre),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE contabilidad_presupuestos
            SET origen='generador_operativo', actualizado_en=CURRENT_TIMESTAMP,
                observaciones='Presupuesto orientativo desde reservas, RRHH, recurrentes y supuestos fijos.'
            WHERE id=?
            """,
            (row["id"],),
        )
        return row["id"]
    cur = conn.execute(
        """
        INSERT INTO contabilidad_presupuestos
        (empresa_id, centro_id, anio, nombre, estado, origen, observaciones)
        VALUES (?, ?, ?, ?, 'borrador', 'generador_operativo',
                'Presupuesto orientativo desde reservas, RRHH, recurrentes y supuestos fijos.')
        """,
        (empresa_id, centro_id, anio, nombre),
    )
    return cur.lastrowid


def _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, tipo, bloque, concepto, importe, cuenta, fuente, fiabilidad, obs=""):
    importe_num = round(_to_float(importe, 0.0), 2)
    if importe_num == 0 and fiabilidad != "pendiente":
        return
    conn.execute(
        """
        INSERT INTO contabilidad_presupuesto_lineas
        (presupuesto_id, empresa_id, centro_id, anio, mes, tipo, bloque, concepto,
         importe, cuenta_contable, fuente, fiabilidad, observaciones)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            presupuesto_id, empresa_id, centro_id, anio, mes, tipo, bloque, concepto,
            importe_num, cuenta, fuente, fiabilidad, obs,
        ),
    )


def _monthly_rows(conn, table, empresa_id, centro_id, anio, expressions):
    if not _table_exists(conn, table):
        return {}
    rows = conn.execute(
        f"""
        SELECT CAST(strftime('%m', fecha) AS INTEGER) AS mes, {", ".join(expressions)}
        FROM {table}
        WHERE empresa_id=? AND COALESCE(centro_id,0)=COALESCE(?,0)
          AND CAST(strftime('%Y', fecha) AS INTEGER)=?
        GROUP BY CAST(strftime('%m', fecha) AS INTEGER)
        """,
        (empresa_id, centro_id, anio),
    ).fetchall()
    return {int(row["mes"]): dict(row) for row in rows}


def _generar_presupuesto_operativo(conn, empresa_id, centro_id, anio):
    presupuesto_id = _presupuesto_get_or_create(conn, empresa_id, centro_id, anio)
    conn.execute("DELETE FROM contabilidad_presupuesto_lineas WHERE presupuesto_id=?", (presupuesto_id,))

    ingresos_reservas = {}
    if _table_exists(conn, "reservas_recepcion"):
        rows = conn.execute(
            """
            SELECT CAST(strftime('%m', fecha_entrada) AS INTEGER) AS mes,
                   COALESCE(SUM(precio_total), 0) AS ingresos,
                   COUNT(*) AS reservas,
                   COALESCE(SUM(adultos + menores), 0) AS pax
            FROM reservas_recepcion
            WHERE empresa_id=? AND COALESCE(centro_id,0)=COALESCE(?,0)
              AND CAST(strftime('%Y', fecha_entrada) AS INTEGER)=?
              AND COALESCE(estado,'') NOT IN ('cancelada','no_show')
            GROUP BY CAST(strftime('%m', fecha_entrada) AS INTEGER)
            """,
            (empresa_id, centro_id, anio),
        ).fetchall()
        ingresos_reservas = {int(row["mes"]): dict(row) for row in rows}

    imserso = _monthly_rows(conn, "imserso_economia_diaria", empresa_id, centro_id, anio, [
        "COALESCE(SUM(ingreso_imserso),0) AS ingresos",
        "COALESCE(SUM(presupuesto_comida),0) AS comida",
        "COALESCE(SUM(presupuesto_personal),0) AS personal",
        "COALESCE(SUM(presupuesto_limpieza_lenceria),0) AS limpieza",
        "COALESCE(SUM(presupuesto_suministros),0) AS suministros",
        "COALESCE(SUM(presupuesto_mantenimiento),0) AS mantenimiento",
        "COALESCE(SUM(presupuesto_alojamiento_estructura),0) AS estructura",
    ])
    normal = _monthly_rows(conn, "albarracin_economia_segmento_diaria", empresa_id, centro_id, anio, [
        "COALESCE(SUM(ingreso_alojamiento_desayuno),0) AS ingresos",
        "COALESCE(SUM(coste_desayuno),0) AS desayuno",
        "COALESCE(SUM(coste_limpieza_lenceria),0) AS limpieza",
        "COALESCE(SUM(coste_suministros),0) AS suministros",
        "COALESCE(SUM(coste_comision),0) AS comisiones",
    ])

    recurrentes = {m: [] for m in range(1, 13)}
    if _table_exists(conn, "contabilidad_recurrentes"):
        rows = conn.execute(
            """
            SELECT nombre, cuenta_contable, total, frecuencia
            FROM contabilidad_recurrentes
            WHERE empresa_id=? AND COALESCE(centro_id,0)=COALESCE(?,0) AND COALESCE(activo,1)=1
            """,
            (empresa_id, centro_id),
        ).fetchall()
        for row in rows:
            frecuencia = (row["frecuencia"] or "mensual").lower()
            meses = range(1, 13)
            if frecuencia == "trimestral":
                meses = (1, 4, 7, 10)
            elif frecuencia == "semestral":
                meses = (1, 7)
            elif frecuencia == "anual":
                meses = (1,)
            for mes in meses:
                recurrentes[int(mes)].append(row)

    costes_fijos_base = [
        ("Luz", "628", 4200.0),
        ("Agua", "628", 650.0),
        ("Gas / energia termica", "628", 1800.0),
        ("Telefono e internet", "628", 280.0),
        ("Seguros", "625", 700.0),
        ("Gestoria / asesoria", "623", 450.0),
        ("IBI e impuestos municipales", "629", 900.0),
        ("Mantenimiento contratado", "622", 850.0),
        ("Software, TPV y pasarelas", "629", 350.0),
        ("Comisiones bancarias", "626", 180.0),
    ]

    coste_laboral_mensual = {}
    coste_laboral_origen = "sin_datos"
    if _table_exists(conn, "resumen_prenomina") and _table_exists(conn, "empleados"):
        rows = conn.execute(
            """
            SELECT rp.mes, COALESCE(SUM(rp.coste_estimado),0) AS coste
            FROM resumen_prenomina rp
            JOIN empleados e ON e.id=rp.empleado_id
            WHERE rp.empresa_id=? AND COALESCE(e.centro_id,0)=COALESCE(?,0) AND rp.anio=?
            GROUP BY rp.mes
            """,
            (empresa_id, centro_id, anio),
        ).fetchall()
        coste_laboral_mensual = {int(row["mes"]): float(row["coste"] or 0) for row in rows}
        if any(coste_laboral_mensual.values()):
            coste_laboral_origen = "prenomina"
    if not any(coste_laboral_mensual.values()) and _table_exists(conn, "contratos") and _table_exists(conn, "empleados"):
        row = conn.execute(
            """
            SELECT COALESCE(SUM(
                CASE
                    WHEN COALESCE(c.salario_bruto_mensual,0)>0 THEN c.salario_bruto_mensual * 1.33
                    WHEN COALESCE(c.salario_bruto_anual,0)>0 THEN (c.salario_bruto_anual / 12.0) * 1.33
                    WHEN COALESCE(c.salario_bruto,0)>0 THEN c.salario_bruto * 1.33
                    ELSE 0
                END
            ),0) AS coste
            FROM contratos c
            JOIN empleados e ON e.id=c.empleado_id
            WHERE c.empresa_id=? AND COALESCE(e.centro_id,0)=COALESCE(?,0) AND COALESCE(c.activo,1)=1
            """,
            (empresa_id, centro_id),
        ).fetchone()
        coste = float(row["coste"] or 0) if row else 0
        if coste:
            coste_laboral_mensual = {m: coste for m in range(1, 13)}
            coste_laboral_origen = "contratos"
    if not any(coste_laboral_mensual.values()) and _table_exists(conn, "empleados"):
        ss_empresa_pct = 0.332
        if _table_exists(conn, "configuracion_prenomina"):
            row = conn.execute(
                "SELECT ss_empresa_pct FROM configuracion_prenomina WHERE empresa_id=? ORDER BY id DESC LIMIT 1",
                (empresa_id,),
            ).fetchone()
            if row and row["ss_empresa_pct"] is not None:
                ss_empresa_pct = _to_float(row["ss_empresa_pct"], 0.332)
                if ss_empresa_pct > 1:
                    ss_empresa_pct = ss_empresa_pct / 100.0
        rows = conn.execute(
            """
            SELECT e.rol, e.nombre, e.apellidos, COALESCE(d.nombre, '') AS departamento
            FROM empleados e
            LEFT JOIN departamentos d ON d.id=e.departamento_id
            WHERE e.empresa_id=? AND COALESCE(e.centro_id,0)=COALESCE(?,0) AND COALESCE(e.activo,1)=1
            """,
            (empresa_id, centro_id),
        ).fetchall()
        bruto_total = 0.0
        for row in rows:
            texto = f"{row['rol'] or ''} {row['departamento'] or ''} {row['nombre'] or ''} {row['apellidos'] or ''}".lower()
            bruto = 1650.0
            if any(palabra in texto for palabra in ("director", "direccion", "gerente")):
                bruto = 2600.0
            elif any(palabra in texto for palabra in ("chef", "jefe", "gobernanta", "responsable")):
                bruto = 2100.0
            elif any(palabra in texto for palabra in ("cocin", "recepcion", "mantenimiento", "tecnico")):
                bruto = 1800.0
            elif any(palabra in texto for palabra in ("ayudante", "pinche", "limpieza", "pisos")):
                bruto = 1500.0
            bruto_total += bruto
        if bruto_total:
            coste = bruto_total * (1.0 + ss_empresa_pct)
            coste_laboral_mensual = {m: round(coste, 2) for m in range(1, 13)}
            coste_laboral_origen = "empleados_activos"

    for mes in range(1, 13):
        normal_mes = normal.get(mes) or {}
        imserso_mes = imserso.get(mes) or {}
        ingreso_imserso = float(imserso_mes.get("ingresos", 0) or 0)
        ingreso_normal = float(normal_mes.get("ingresos", 0) or 0)
        if ingreso_imserso or ingreso_normal:
            _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "ingreso", "Alojamiento", "Ingreso IMSERSO / Mundiplan", ingreso_imserso, "705", "imserso_economia_diaria", "simulado" if ingreso_imserso else "pendiente", "Ingreso repartido por dia de estancia: 45 EUR pax/dia PC.")
            _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "ingreso", "Alojamiento", "Ingreso cliente normal", ingreso_normal, "705", "albarracin_economia_segmento_diaria", "simulado" if ingreso_normal else "pendiente", "Ingreso repartido por pax/noche segun tarifa Albarracin.")
        else:
            ingreso = float((ingresos_reservas.get(mes) or {}).get("ingresos") or 0)
            _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "ingreso", "Alojamiento", "Reservas previstas", ingreso, "705", "reservas_recepcion", "simulado" if ingreso else "pendiente", "Ingreso asignado por mes de entrada.")
        _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Comida y bebida", "Presupuesto comida Imserso", imserso_mes.get("comida", 0), "600", "imserso_economia_diaria", "estimado")
        _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Comida y bebida", "Desayunos cliente normal", normal_mes.get("desayuno", 0), "600", "albarracin_economia_segmento_diaria", "estimado")
        _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Pisos y lavanderia", "Limpieza y lenceria", float(imserso_mes.get("limpieza", 0) or 0) + float(normal_mes.get("limpieza", 0) or 0), "602", "economia_segmentos", "estimado")
        _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Comercial", "Comisiones de canal", normal_mes.get("comisiones", 0), "627", "albarracin_economia_segmento_diaria", "estimado")

        laboral = coste_laboral_mensual.get(mes, 0.0)
        obs_laboral = "" if laboral else "Hay empleados, pero faltan contratos/prenominas con coste."
        fiabilidad_laboral = "estimado" if laboral else "pendiente"
        if laboral and coste_laboral_origen == "empleados_activos":
            fiabilidad_laboral = "supuesto"
            obs_laboral = "Estimado por empleados activos. Sustituir por contrato, nomina o pre-nomina real."
        _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Personal", "Coste laboral RRHH" if laboral else "Coste laboral pendiente de contratos", laboral, "640", "rrhh", fiabilidad_laboral, obs_laboral)

        if recurrentes[mes]:
            for row in recurrentes[mes]:
                _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Recurrentes", row["nombre"], row["total"], row["cuenta_contable"] or "629", "contabilidad_recurrentes", "configurado")
        recurrentes_texto = " ".join((row["nombre"] or "").lower() for row in recurrentes[mes])
        tokens_coste_fijo = {
            "Luz": ("luz", "electric"),
            "Agua": ("agua",),
            "Gas / energia termica": ("gas", "energia termica"),
            "Telefono e internet": ("telefono", "internet", "fibra"),
            "Seguros": ("seguro",),
            "Gestoria / asesoria": ("gestoria", "asesoria", "asesor"),
            "IBI e impuestos municipales": ("ibi", "impuesto municipal", "tributo"),
            "Mantenimiento contratado": ("mantenimiento", "reparacion"),
            "Software, TPV y pasarelas": ("software", "tpv", "pasarela"),
            "Comisiones bancarias": ("comision bancaria", "banco"),
        }
        for concepto, cuenta, importe in costes_fijos_base:
            tokens = tokens_coste_fijo.get(concepto, (concepto.lower(),))
            if recurrentes_texto and any(token in recurrentes_texto for token in tokens):
                continue
            _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Costes fijos", concepto, importe, cuenta, "supuesto_fijo", "supuesto", "Sustituir por recurrente/factura real cuando exista.")

        _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Suministros variables", "Suministros ligados a ocupacion", float(imserso_mes.get("suministros", 0) or 0) + float(normal_mes.get("suministros", 0) or 0), "628", "economia_segmentos", "estimado")
        _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Mantenimiento", "Mantenimiento variable Imserso", imserso_mes.get("mantenimiento", 0), "622", "imserso_economia_diaria", "estimado")
        _presupuesto_add_line(conn, presupuesto_id, empresa_id, centro_id, anio, mes, "gasto", "Estructura", "Estructura/alojamiento imputada Imserso", imserso_mes.get("estructura", 0), "629", "imserso_economia_diaria", "estimado")

    return presupuesto_id


def _anio_presupuesto_por_defecto(conn, empresa_id, centro_id):
    candidatos = []
    if _table_exists(conn, "reservas_recepcion"):
        if "centro_id" in _columns(conn, "reservas_recepcion"):
            row = conn.execute(
                """
                SELECT MAX(CAST(strftime('%Y', fecha_entrada) AS INTEGER)) AS anio
                FROM reservas_recepcion
                WHERE empresa_id=? AND COALESCE(centro_id,0)=COALESCE(?,0)
                """,
                (empresa_id, centro_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT MAX(CAST(strftime('%Y', fecha_entrada) AS INTEGER)) AS anio
                FROM reservas_recepcion
                WHERE empresa_id=?
                """,
                (empresa_id,),
            ).fetchone()
        if row and row["anio"]:
            candidatos.append(int(row["anio"]))
    for table in ("imserso_economia_diaria", "albarracin_economia_segmento_diaria"):
        if _table_exists(conn, table):
            if "centro_id" in _columns(conn, table):
                row = conn.execute(
                    f"""
                    SELECT MAX(CAST(strftime('%Y', fecha) AS INTEGER)) AS anio
                    FROM {table}
                    WHERE empresa_id=? AND COALESCE(centro_id,0)=COALESCE(?,0)
                    """,
                    (empresa_id, centro_id),
                ).fetchone()
            else:
                row = conn.execute(
                    f"""
                    SELECT MAX(CAST(strftime('%Y', fecha) AS INTEGER)) AS anio
                    FROM {table}
                    WHERE empresa_id=?
                    """,
                    (empresa_id,),
                ).fetchone()
            if row and row["anio"]:
                candidatos.append(int(row["anio"]))
    return max(candidatos) if candidatos else datetime.now().year


@contabilidad_bp.route('/')
@login_required
def dashboard():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()

    hoy = datetime.now().strftime("%Y-%m-%d")
    inicio_mes = datetime.now().strftime("%Y-%m-01")
    scope_doc_sql, scope_doc_params = _scope_contabilidad_documentos("contabilidad_documentos", conn, empresa_id)
    scope_d_sql, scope_d_params = _scope_contabilidad_documentos("d", conn, empresa_id)
    resumen = conn.execute(f"""
        SELECT
            COALESCE(SUM(CASE WHEN estado IN ('pendiente_pago', 'pagado_parcial', 'vencido', 'bloqueado') THEN pendiente ELSE 0 END), 0) AS pendiente,
            COALESCE(SUM(CASE WHEN estado = 'pendiente_factura' THEN total ELSE 0 END), 0) AS pendiente_factura,
            COALESCE(SUM(CASE WHEN estado = 'vencido' THEN pendiente ELSE 0 END), 0) AS vencido,
            COALESCE(SUM(CASE WHEN fecha_documento >= ? AND tipo NOT IN ('factura_venta', 'ticket_venta') THEN total ELSE 0 END), 0) AS gastos_mes,
            COALESCE(SUM(CASE WHEN fecha_documento >= ? AND tipo IN ('factura_venta', 'ticket_venta') THEN total ELSE 0 END), 0) AS ingresos_mes,
            COUNT(CASE WHEN estado <> 'pagado' THEN 1 END) AS docs_pendientes
        FROM contabilidad_documentos
        WHERE {scope_doc_sql}
    """, (inicio_mes, inicio_mes, *scope_doc_params)).fetchone()
    vencimientos = conn.execute(f"""
        SELECT d.*, t.nombre AS tercero_nombre
        FROM contabilidad_documentos d
        LEFT JOIN contabilidad_terceros t ON t.id = d.tercero_id
        WHERE {scope_d_sql}
          AND d.estado IN ('pendiente_pago', 'pagado_parcial', 'vencido', 'bloqueado')
        ORDER BY date(COALESCE(d.fecha_vencimiento, d.fecha_documento, ?)), d.id DESC
        LIMIT 8
    """, (*scope_d_params, hoy)).fetchall()
    por_tipo = conn.execute(f"""
        SELECT tipo, COUNT(*) AS total_docs, COALESCE(SUM(total), 0) AS total_importe
        FROM contabilidad_documentos
        WHERE {scope_doc_sql}
        GROUP BY tipo
        ORDER BY total_importe DESC
    """, scope_doc_params).fetchall()
    recientes = conn.execute(f"""
        SELECT d.*, t.nombre AS tercero_nombre
        FROM contabilidad_documentos d
        LEFT JOIN contabilidad_terceros t ON t.id = d.tercero_id
        WHERE {scope_d_sql}
        ORDER BY d.fecha_creacion DESC, d.id DESC
        LIMIT 12
    """, scope_d_params).fetchall()
    conn.close()
    return render_template(
        'contabilidad/dashboard.html',
        resumen=resumen,
        vencimientos=vencimientos,
        por_tipo=por_tipo,
        recientes=recientes,
    )


@contabilidad_bp.route('/presupuesto')
@login_required
def presupuesto():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    _ensure_schema(conn)
    conn.commit()

    try:
        anio = int(request.args.get("anio") or _anio_presupuesto_por_defecto(conn, empresa_id, centro_id))
    except Exception:
        anio = _anio_presupuesto_por_defecto(conn, empresa_id, centro_id)

    presupuesto_row = conn.execute(
        """
        SELECT *
        FROM contabilidad_presupuestos
        WHERE empresa_id=? AND COALESCE(centro_id,0)=COALESCE(?,0) AND anio=?
        ORDER BY actualizado_en DESC, id DESC
        LIMIT 1
        """,
        (empresa_id, centro_id, anio),
    ).fetchone()

    meses = []
    bloques = []
    lineas = []
    lineas_resumen = []
    resumen = {
        "ingresos": 0.0,
        "gastos": 0.0,
        "resultado": 0.0,
        "pendientes": 0,
        "supuestos": 0,
        "lineas": 0,
    }

    if presupuesto_row:
        resumen_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN tipo='ingreso' THEN importe ELSE 0 END),0) AS ingresos,
                COALESCE(SUM(CASE WHEN tipo='gasto' THEN importe ELSE 0 END),0) AS gastos,
                COALESCE(SUM(CASE WHEN tipo='ingreso' THEN importe ELSE -importe END),0) AS resultado,
                COALESCE(SUM(CASE WHEN fiabilidad='pendiente' THEN 1 ELSE 0 END),0) AS pendientes,
                COALESCE(SUM(CASE WHEN fiabilidad='supuesto' THEN 1 ELSE 0 END),0) AS supuestos,
                COUNT(*) AS lineas
            FROM contabilidad_presupuesto_lineas
            WHERE presupuesto_id=?
            """,
            (presupuesto_row["id"],),
        ).fetchone()
        resumen = dict(resumen_row)

        meses = conn.execute(
            """
            SELECT
                mes,
                COALESCE(SUM(CASE WHEN tipo='ingreso' THEN importe ELSE 0 END),0) AS ingresos,
                COALESCE(SUM(CASE WHEN tipo='gasto' THEN importe ELSE 0 END),0) AS gastos,
                COALESCE(SUM(CASE WHEN tipo='ingreso' THEN importe ELSE -importe END),0) AS resultado,
                COALESCE(SUM(CASE WHEN fiabilidad='pendiente' THEN 1 ELSE 0 END),0) AS pendientes,
                COALESCE(SUM(CASE WHEN fiabilidad='supuesto' THEN 1 ELSE 0 END),0) AS supuestos
            FROM contabilidad_presupuesto_lineas
            WHERE presupuesto_id=?
            GROUP BY mes
            ORDER BY mes
            """,
            (presupuesto_row["id"],),
        ).fetchall()

        bloques = conn.execute(
            """
            SELECT
                tipo,
                bloque,
                COALESCE(SUM(importe),0) AS importe,
                COALESCE(SUM(CASE WHEN fiabilidad='pendiente' THEN 1 ELSE 0 END),0) AS pendientes,
                COALESCE(SUM(CASE WHEN fiabilidad='supuesto' THEN 1 ELSE 0 END),0) AS supuestos
            FROM contabilidad_presupuesto_lineas
            WHERE presupuesto_id=?
            GROUP BY tipo, bloque
            ORDER BY tipo DESC, importe DESC
            """,
            (presupuesto_row["id"],),
        ).fetchall()

        lineas = conn.execute(
            """
            SELECT *
            FROM contabilidad_presupuesto_lineas
            WHERE presupuesto_id=?
            ORDER BY mes, CASE WHEN tipo='ingreso' THEN 0 ELSE 1 END, bloque, concepto
            """,
            (presupuesto_row["id"],),
        ).fetchall()
        lineas_resumen = conn.execute(
            """
            SELECT
                tipo,
                bloque,
                concepto,
                fuente,
                fiabilidad,
                COALESCE(SUM(importe),0) AS importe,
                COUNT(*) AS meses,
                MIN(mes) AS mes_desde,
                MAX(mes) AS mes_hasta,
                MAX(observaciones) AS observaciones
            FROM contabilidad_presupuesto_lineas
            WHERE presupuesto_id=?
            GROUP BY tipo, bloque, concepto, fuente, fiabilidad
            ORDER BY CASE WHEN tipo='ingreso' THEN 0 ELSE 1 END, bloque, importe DESC
            """,
            (presupuesto_row["id"],),
        ).fetchall()

    centro = None
    if centro_id and _table_exists(conn, "centros_trabajo"):
        centro = conn.execute(
            "SELECT id, nombre FROM centros_trabajo WHERE id=? AND empresa_id=?",
            (centro_id, empresa_id),
        ).fetchone()

    conn.close()
    return render_template(
        "contabilidad/presupuesto.html",
        anio=anio,
        presupuesto=presupuesto_row,
        resumen=resumen,
        meses=meses,
        bloques=bloques,
        lineas=lineas,
        lineas_resumen=lineas_resumen,
        detalle_mode=request.args.get("detalle") or "resumen",
        centro=centro,
    )


@contabilidad_bp.route('/presupuesto/generar', methods=['POST'])
@login_required
def generar_presupuesto():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    _ensure_schema(conn)
    try:
        anio = int(request.form.get("anio") or _anio_presupuesto_por_defecto(conn, empresa_id, centro_id))
    except Exception:
        anio = _anio_presupuesto_por_defecto(conn, empresa_id, centro_id)
    presupuesto_id = _generar_presupuesto_operativo(conn, empresa_id, centro_id, anio)
    conn.commit()
    conn.close()
    flash(f"Presupuesto operativo {anio} generado con datos reales, estimados y supuestos identificados.", "success")
    return redirect(url_for("contabilidad.presupuesto", anio=anio, presupuesto_id=presupuesto_id))


@contabilidad_bp.route('/documentos')
@login_required
def documentos():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    estado = (request.args.get('estado') or '').strip()
    tipo = (request.args.get('tipo') or '').strip()
    buscar = (request.args.get('buscar') or '').strip()
    scope_sql, scope_params = _scope_contabilidad_documentos("d", conn, empresa_id)
    where = [scope_sql]
    params = list(scope_params)
    if estado:
        where.append("d.estado = ?")
        params.append(estado)
    if tipo:
        where.append("d.tipo = ?")
        params.append(tipo)
    if buscar:
        where.append("(COALESCE(d.numero, '') LIKE ? OR COALESCE(t.nombre, '') LIKE ? OR COALESCE(d.observaciones, '') LIKE ?)")
        like = f"%{buscar}%"
        params.extend([like, like, like])
    rows = conn.execute(f"""
        SELECT d.*, t.nombre AS tercero_nombre, t.codigo_contable
        FROM contabilidad_documentos d
        LEFT JOIN contabilidad_terceros t ON t.id = d.tercero_id
        WHERE {" AND ".join(where)}
        ORDER BY date(COALESCE(d.fecha_documento, d.fecha_creacion)) DESC, d.id DESC
        LIMIT 300
    """, params).fetchall()
    conn.close()
    return render_template(
        'contabilidad/documentos.html',
        documentos=rows,
        estados=ESTADOS_DOCUMENTO,
        tipos=TIPOS_DOCUMENTO,
        filtros={"estado": estado, "tipo": tipo, "buscar": buscar},
    )


@contabilidad_bp.route('/documentos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_documento():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()

    if request.method == 'POST':
        centro_id = centro_activo_id()
        tipo = request.form.get('tipo') or 'factura_compra'
        numero = (request.form.get('numero') or '').strip()
        tercero_id = _to_int_or_none(request.form.get('tercero_id'))
        proveedor_id = _to_int_or_none(request.form.get('proveedor_id'))
        if not tercero_id and proveedor_id:
            tercero_id = _ensure_tercero_proveedor(conn, proveedor_id, empresa_id)
        if not tercero_id and (request.form.get('tercero_nombre') or '').strip():
            nombre = (request.form.get('tercero_nombre') or '').strip()
            cur = conn.execute("""
                INSERT INTO contabilidad_terceros
                (tipo, nombre, nif, codigo_contable, empresa_id, centro_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                request.form.get('tercero_tipo') or 'acreedor',
                nombre,
                (request.form.get('tercero_nif') or '').strip(),
                (request.form.get('codigo_contable') or '').strip(),
                empresa_id,
                centro_id,
            ))
            tercero_id = cur.lastrowid

        base = _to_float(request.form.get('base_imponible'), 0.0)
        impuesto = _to_float(request.form.get('impuesto_importe'), 0.0)
        total = _to_float(request.form.get('total'), base + impuesto)
        pagado = _to_float(request.form.get('pagado'), 0.0)
        pendiente = max(total - pagado, 0.0)
        estado = request.form.get('estado') or ('pagado' if pendiente <= 0.009 else 'pendiente_pago')

        cur = conn.execute("""
            INSERT INTO contabilidad_documentos (
                tipo, numero, tercero_id, proveedor_id, fecha_documento, fecha_vencimiento,
                estado, base_imponible, impuesto_importe, total, pagado, pendiente,
                cuenta_contable, centro_coste, departamento_id, subdepartamento_id,
                punto_venta_id, observaciones, creado_por, empresa_id, centro_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tipo,
            numero or None,
            tercero_id,
            proveedor_id,
            request.form.get('fecha_documento') or datetime.now().strftime("%Y-%m-%d"),
            request.form.get('fecha_vencimiento') or None,
            estado,
            round(base, 2),
            round(impuesto, 2),
            round(total, 2),
            round(pagado, 2),
            round(pendiente, 2),
            request.form.get('cuenta_contable') or None,
            request.form.get('centro_coste') or None,
            _to_int_or_none(request.form.get('departamento_id')),
            _to_int_or_none(request.form.get('subdepartamento_id')),
            _to_int_or_none(request.form.get('punto_venta_id')),
            request.form.get('observaciones') or None,
            session.get('user_nombre') or 'admin',
            empresa_id,
            centro_id,
        ))
        documento_id = cur.lastrowid

        concepto = (request.form.get('concepto') or '').strip() or dict(TIPOS_DOCUMENTO).get(tipo, tipo)
        impuesto_tipo = _to_float(request.form.get('impuesto_tipo'), 0.0)
        conn.execute("""
            INSERT INTO contabilidad_lineas_documento (
                documento_id, concepto, cantidad, precio_unitario, base,
                impuesto_tipo, impuesto_importe, total, cuenta_contable,
                departamento_id, subdepartamento_id, punto_venta_id, empresa_id
            )
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            documento_id,
            concepto,
            round(base, 2),
            round(base, 2),
            impuesto_tipo,
            round(impuesto, 2),
            round(total, 2),
            request.form.get('cuenta_contable') or None,
            _to_int_or_none(request.form.get('departamento_id')),
            _to_int_or_none(request.form.get('subdepartamento_id')),
            _to_int_or_none(request.form.get('punto_venta_id')),
            empresa_id,
        ))
        if pagado > 0:
            conn.execute("""
                INSERT INTO contabilidad_pagos
                (documento_id, fecha_pago, metodo, importe, cuenta_tesoreria, usuario, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                documento_id,
                request.form.get('fecha_documento') or datetime.now().strftime("%Y-%m-%d"),
                request.form.get('metodo_pago') or 'caja',
                round(pagado, 2),
                request.form.get('cuenta_tesoreria') or '570',
                session.get('user_nombre') or 'admin',
                empresa_id,
            ))
            _actualizar_estado_documento(conn, documento_id, empresa_id)

        conn.commit()
        conn.close()
        flash('Documento economico creado.', 'success')
        return redirect(url_for('contabilidad.ver_documento', documento_id=documento_id))

    opciones = _opciones_base(conn, empresa_id)
    conn.close()
    return render_template(
        'contabilidad/documento_form.html',
        tipos=TIPOS_DOCUMENTO,
        estados=ESTADOS_DOCUMENTO,
        opciones=opciones,
        now=datetime.now(),
    )


@contabilidad_bp.route('/documentos/<int:documento_id>')
@login_required
def ver_documento(documento_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    scope_sql, scope_params = _scope_contabilidad_documentos("d", conn, empresa_id)
    documento = conn.execute(f"""
        SELECT d.*, t.nombre AS tercero_nombre, t.nif AS tercero_nif, t.codigo_contable,
               p.nombre AS proveedor_nombre, dep.nombre AS departamento_nombre,
               sub.nombre AS subdepartamento_nombre, pv.nombre AS punto_venta_nombre
        FROM contabilidad_documentos d
        LEFT JOIN contabilidad_terceros t ON t.id = d.tercero_id
        LEFT JOIN proveedores p ON p.id = d.proveedor_id
        LEFT JOIN departamentos dep ON dep.id = d.departamento_id
        LEFT JOIN subdepartamentos sub ON sub.id = d.subdepartamento_id
        LEFT JOIN puntos_venta pv ON pv.id = d.punto_venta_id
        WHERE d.id = ? AND {scope_sql}
    """, (documento_id, *scope_params)).fetchone()
    if not documento:
        conn.close()
        flash('Documento no encontrado.', 'danger')
        return redirect(url_for('contabilidad.documentos'))
    lineas = conn.execute("""
        SELECT * FROM contabilidad_lineas_documento
        WHERE documento_id = ?
        ORDER BY id
    """, (documento_id,)).fetchall()
    pagos = conn.execute("""
        SELECT * FROM contabilidad_pagos
        WHERE documento_id = ?
        ORDER BY fecha_pago DESC, id DESC
    """, (documento_id,)).fetchall()
    conn.close()
    return render_template('contabilidad/ver_documento.html', documento=documento, lineas=lineas, pagos=pagos)


@contabilidad_bp.route('/documentos/<int:documento_id>/pagar', methods=['POST'])
@login_required
def registrar_pago(documento_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    scope_sql, scope_params = _scope_contabilidad_documentos("d", conn, empresa_id)
    documento = conn.execute(f"""
        SELECT id, pendiente
        FROM contabilidad_documentos d
        WHERE id = ? AND {scope_sql}
    """, (documento_id, *scope_params)).fetchone()
    if not documento:
        conn.close()
        flash('Documento no encontrado.', 'danger')
        return redirect(url_for('contabilidad.documentos'))
    importe = _to_float(request.form.get('importe'), _to_float(documento["pendiente"], 0.0))
    if importe <= 0:
        conn.close()
        flash('El importe del pago debe ser mayor que cero.', 'warning')
        return redirect(url_for('contabilidad.ver_documento', documento_id=documento_id))
    conn.execute("""
        INSERT INTO contabilidad_pagos
        (documento_id, fecha_pago, metodo, importe, cuenta_tesoreria, referencia, observaciones, usuario, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        documento_id,
        request.form.get('fecha_pago') or datetime.now().strftime("%Y-%m-%d"),
        request.form.get('metodo') or 'transferencia',
        round(importe, 2),
        request.form.get('cuenta_tesoreria') or '572',
        request.form.get('referencia') or None,
        request.form.get('observaciones') or None,
        session.get('user_nombre') or 'admin',
        empresa_id,
    ))
    _actualizar_estado_documento(conn, documento_id, empresa_id)
    conn.commit()
    conn.close()
    flash('Pago registrado.', 'success')
    return redirect(url_for('contabilidad.ver_documento', documento_id=documento_id))


@contabilidad_bp.route('/terceros', methods=['GET', 'POST'])
@login_required
def terceros():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    if request.method == 'POST':
        conn.execute("""
            INSERT INTO contabilidad_terceros
            (tipo, nombre, nif, email, telefono, codigo_contable, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            request.form.get('tipo') or 'acreedor',
            (request.form.get('nombre') or '').strip(),
            (request.form.get('nif') or '').strip(),
            (request.form.get('email') or '').strip(),
            (request.form.get('telefono') or '').strip(),
            (request.form.get('codigo_contable') or '').strip(),
            empresa_id,
            centro_activo_id(),
        ))
        conn.commit()
        flash('Tercero creado.', 'success')
        return redirect(url_for('contabilidad.terceros'))
    rows = conn.execute("""
        SELECT *
        FROM contabilidad_terceros
        WHERE empresa_id = ? AND activo = 1
          """ + ("AND centro_id = ?" if centro_activo_id() else "") + """
        ORDER BY tipo, nombre
    """, [empresa_id] + ([centro_activo_id()] if centro_activo_id() else [])).fetchall()
    conn.close()
    return render_template('contabilidad/terceros.html', terceros=rows)


@contabilidad_bp.route('/cuentas')
@login_required
def cuentas():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    rows = conn.execute("""
        SELECT *
        FROM contabilidad_cuentas
        WHERE empresa_id = ?
        ORDER BY codigo
    """, (empresa_id,)).fetchall()
    conn.close()
    return render_template('contabilidad/cuentas.html', cuentas=rows)


@contabilidad_bp.route('/sincronizar-compras', methods=['POST'])
@login_required
def sincronizar_compras():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    if not _table_exists(conn, "recepciones") or not _table_exists(conn, "lineas_recepcion"):
        conn.close()
        flash('No existe todavia la estructura de recepciones de compras.', 'warning')
        return redirect(url_for('contabilidad.dashboard'))

    cols_recepciones = _columns(conn, "recepciones")
    validacion_sql = "AND COALESCE(r.validada_economicamente, 0) = 1" if "validada_economicamente" in cols_recepciones else ""
    centro_id = centro_activo_id()
    centro_sql = ""
    centro_params = []
    if centro_id:
        centro_condiciones = []
        dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
        if dep_ids:
            deps_placeholders = ",".join("?" for _ in dep_ids)
            centro_condiciones.append(f"COALESCE(r.departamento_receptor_id, r.departamento_id) IN ({deps_placeholders})")
            centro_params.extend(dep_ids)
        pv_ids = ids_puntos_venta_centro(conn, empresa_id, centro_id)
        if pv_ids:
            pvs_placeholders = ",".join("?" for _ in pv_ids)
            centro_condiciones.append(f"r.punto_venta_id IN ({pvs_placeholders})")
            centro_params.extend(pv_ids)
        centro_sql = f"AND ({' OR '.join(centro_condiciones)})" if centro_condiciones else "AND 1 = 0"
    rows = conn.execute(f"""
        SELECT r.id, r.proveedor_id, r.fecha_recepcion, r.numero_albaran,
               r.departamento_id, r.departamento_receptor_id, r.subdepartamento_receptor_id,
               r.punto_venta_id, r.observaciones,
               r.documento_economico_tipo, r.estado_facturacion,
               r.fecha_vencimiento_economica, r.forma_pago_economica,
               pr.nombre AS proveedor_nombre,
               COALESCE(SUM(COALESCE(lr.importe_total, COALESCE(lr.cantidad_recibida, 0) * COALESCE(lr.precio_unitario, 0))), 0) AS total
        FROM recepciones r
        LEFT JOIN proveedores pr ON pr.id = r.proveedor_id
        LEFT JOIN lineas_recepcion lr ON lr.recepcion_id = r.id
        WHERE r.empresa_id = ?
          {validacion_sql}
          {centro_sql}
          AND NOT EXISTS (
              SELECT 1
              FROM contabilidad_documentos d
              WHERE d.empresa_id = r.empresa_id
                AND d.origen_modulo = 'compras'
                AND d.origen_tipo = 'recepcion'
                AND d.origen_id = r.id
          )
        GROUP BY r.id
        ORDER BY r.fecha_recepcion
    """, [empresa_id] + centro_params).fetchall()

    creados = 0
    for r in rows:
        total = _to_float(r["total"], 0.0)
        if total <= 0:
            continue
        tercero_id = _ensure_tercero_proveedor(conn, r["proveedor_id"], empresa_id)
        documento_tipo_recepcion = (r["documento_economico_tipo"] or "albaran").strip().lower()
        if documento_tipo_recepcion in {"factura", "factura_compra"}:
            documento_tipo = "factura_compra"
            estado = "pendiente_pago"
        elif documento_tipo_recepcion in {"ticket", "factura_simplificada"}:
            documento_tipo = "ticket"
            estado = "pendiente_pago"
        else:
            documento_tipo = "albaran"
            estado = "pendiente_factura"
        fecha_vencimiento = r["fecha_vencimiento_economica"] or r["fecha_recepcion"]
        base = round(total, 2)
        impuesto = 0.0
        departamento_doc_id = r["departamento_receptor_id"] or r["departamento_id"]
        centro_doc_id = _centro_desde_departamento_o_pv(conn, departamento_doc_id, r["punto_venta_id"])
        cur = conn.execute("""
            INSERT INTO contabilidad_documentos (
                tipo, numero, tercero_id, proveedor_id, fecha_documento, fecha_vencimiento,
                estado, base_imponible, impuesto_importe, total, pagado, pendiente,
                cuenta_contable, departamento_id, subdepartamento_id, punto_venta_id,
                origen_modulo, origen_tipo, origen_id, observaciones, creado_por, empresa_id, centro_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?,
                    '600', ?, ?, ?, 'compras', 'recepcion', ?, ?, ?, ?, ?)
        """, (
            documento_tipo,
            r["numero_albaran"] or f"RECEP-{r['id']}",
            tercero_id,
            r["proveedor_id"],
            r["fecha_recepcion"],
            fecha_vencimiento,
            estado,
            base,
            impuesto,
            total,
            total,
            departamento_doc_id,
            r["subdepartamento_receptor_id"],
            r["punto_venta_id"],
            r["id"],
            r["observaciones"],
            session.get('user_nombre') or 'admin',
            empresa_id,
            centro_doc_id,
        ))
        documento_id = cur.lastrowid
        conn.execute("""
            UPDATE recepciones
            SET contabilidad_documento_id = ?
            WHERE id = ? AND empresa_id = ?
        """, (documento_id, r["id"], empresa_id))
        lineas = conn.execute("""
            SELECT lr.*, p.nombre AS producto_nombre
            FROM lineas_recepcion lr
            LEFT JOIN productos p ON p.id = lr.producto_id
            WHERE lr.recepcion_id = ?
        """, (r["id"],)).fetchall()
        for linea in lineas:
            linea_total = _to_float(linea["importe_total"], _to_float(linea["cantidad_recibida"], 0.0) * _to_float(linea["precio_unitario"], 0.0))
            linea_base = round(linea_total, 2) if linea_total else 0.0
            conn.execute("""
                INSERT INTO contabilidad_lineas_documento (
                    documento_id, concepto, cantidad, precio_unitario, base,
                    impuesto_tipo, impuesto_importe, total, cuenta_contable, empresa_id
                )
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, '600', ?)
            """, (
                documento_id,
                linea["producto_nombre"] or f"Producto {linea['producto_id']}",
                _to_float(linea["cantidad_recibida"], 0.0),
                _to_float(linea["precio_unitario"], 0.0),
                linea_base,
                0.0,
                round(linea_total, 2),
                empresa_id,
            ))
        creados += 1

    conn.commit()
    conn.close()
    flash(f'Sincronizacion finalizada. Documentos creados: {creados}.', 'success')
    return redirect(url_for('contabilidad.documentos'))


@contabilidad_bp.route('/facturas-proveedor')
@login_required
def facturas_proveedor():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    scope_sql, scope_params = _scope_contabilidad_documentos("f", conn, empresa_id)
    rows = conn.execute(f"""
        SELECT f.*, t.nombre AS tercero_nombre,
               COUNT(a.id) AS albaranes,
               COALESCE(SUM(a.importe_albaran), 0) AS total_albaranes
        FROM contabilidad_documentos f
        LEFT JOIN contabilidad_terceros t ON t.id = f.tercero_id
        LEFT JOIN contabilidad_factura_albaranes a
               ON a.factura_id = f.id AND a.empresa_id = f.empresa_id
        WHERE {scope_sql}
          AND f.origen_modulo = 'contabilidad'
          AND f.origen_tipo = 'factura_albaranes'
        GROUP BY f.id
        ORDER BY date(COALESCE(f.fecha_documento, f.fecha_creacion)) DESC, f.id DESC
        LIMIT 200
    """, scope_params).fetchall()
    conn.close()
    return render_template('contabilidad/facturas_proveedor.html', facturas=rows)


@contabilidad_bp.route('/facturas-proveedor/nueva', methods=['GET', 'POST'])
@login_required
def nueva_factura_proveedor():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()

    if request.method == 'POST':
        proveedor_id = _to_int_or_none(request.form.get('proveedor_id'))
        seleccionados = [
            int(x) for x in request.form.getlist('albaran_ids')
            if str(x).isdigit()
        ]
        numero = (request.form.get('numero_factura') or '').strip()
        if not proveedor_id or not seleccionados or not numero:
            conn.close()
            flash('Elige proveedor, albaranes y numero de factura.', 'warning')
            return redirect(url_for('contabilidad.nueva_factura_proveedor', proveedor_id=proveedor_id or ""))

        placeholders = ",".join("?" for _ in seleccionados)
        scope_sql, scope_params = _scope_contabilidad_documentos("d", conn, empresa_id)
        albaranes = conn.execute(f"""
            SELECT d.*, r.id AS recepcion_id, t.nombre AS tercero_nombre
            FROM contabilidad_documentos d
            LEFT JOIN recepciones r
                   ON r.contabilidad_documento_id = d.id
                  AND r.empresa_id = d.empresa_id
            LEFT JOIN contabilidad_terceros t ON t.id = d.tercero_id
            WHERE d.id IN ({placeholders})
              AND {scope_sql}
              AND d.proveedor_id = ?
              AND d.tipo = 'albaran'
              AND d.estado = 'pendiente_factura'
            ORDER BY date(COALESCE(d.fecha_documento, d.fecha_creacion)), d.id
        """, seleccionados + scope_params + [proveedor_id]).fetchall()
        if not albaranes:
            conn.close()
            flash('No hay albaranes pendientes validos para esa factura.', 'warning')
            return redirect(url_for('contabilidad.nueva_factura_proveedor', proveedor_id=proveedor_id))

        tercero_id = _ensure_tercero_proveedor(conn, proveedor_id, empresa_id)
        suma_albaranes = round(sum(_to_float(a["total"], 0.0) for a in albaranes), 2)
        total_factura = _to_float(request.form.get('total_factura'), suma_albaranes)
        base = _to_float(request.form.get('base_imponible'), total_factura)
        impuesto = _to_float(request.form.get('impuesto_importe'), max(total_factura - base, 0.0))
        diferencia = round(total_factura - suma_albaranes, 2)
        estado = 'pendiente_pago' if abs(diferencia) <= 0.01 else 'pendiente_validar'
        observaciones = (request.form.get('observaciones') or '').strip()
        centro_doc_id = albaranes[0]["centro_id"] if albaranes and "centro_id" in albaranes[0].keys() else centro_activo_id()
        if abs(diferencia) > 0.01:
            aviso = f"Diferencia contra albaranes: {diferencia:.2f} EUR."
            observaciones = f"{observaciones}\n{aviso}".strip()

        cur = conn.execute("""
            INSERT INTO contabilidad_documentos (
                tipo, numero, tercero_id, proveedor_id, fecha_documento, fecha_vencimiento,
                estado, base_imponible, impuesto_importe, total, pagado, pendiente,
                cuenta_contable, origen_modulo, origen_tipo, observaciones, creado_por, empresa_id, centro_id
            )
            VALUES ('factura_compra', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?,
                    ?, 'contabilidad', 'factura_albaranes', ?, ?, ?, ?)
        """, (
            numero,
            tercero_id,
            proveedor_id,
            request.form.get('fecha_documento') or datetime.now().strftime("%Y-%m-%d"),
            request.form.get('fecha_vencimiento') or None,
            estado,
            round(base, 2),
            round(impuesto, 2),
            round(total_factura, 2),
            round(total_factura, 2),
            request.form.get('cuenta_contable') or '600',
            observaciones or None,
            session.get('user_nombre') or 'admin',
            empresa_id,
            centro_doc_id,
        ))
        factura_id = cur.lastrowid

        for albaran in albaranes:
            importe = _to_float(albaran["total"], 0.0)
            conn.execute("""
                INSERT OR IGNORE INTO contabilidad_factura_albaranes
                (factura_id, albaran_id, recepcion_id, importe_albaran, empresa_id)
                VALUES (?, ?, ?, ?, ?)
            """, (factura_id, albaran["id"], albaran["recepcion_id"], round(importe, 2), empresa_id))
            conn.execute("""
                INSERT INTO contabilidad_lineas_documento
                (documento_id, concepto, cantidad, precio_unitario, base, impuesto_tipo,
                 impuesto_importe, total, cuenta_contable, empresa_id)
                VALUES (?, ?, 1, ?, ?, 0, 0, ?, ?, ?)
            """, (
                factura_id,
                f"Albaran {albaran['numero'] or albaran['id']}",
                round(importe, 2),
                round(importe, 2),
                round(importe, 2),
                albaran["cuenta_contable"] or request.form.get('cuenta_contable') or '600',
                empresa_id,
            ))
            conn.execute("""
                UPDATE contabilidad_documentos
                SET estado = 'facturado', pendiente = 0
                WHERE id = ? AND empresa_id = ?
            """, (albaran["id"], empresa_id))
            if albaran["recepcion_id"]:
                conn.execute("""
                    UPDATE recepciones
                    SET estado_facturacion = 'facturado',
                        contabilidad_factura_id = ?
                    WHERE id = ? AND empresa_id = ?
                """, (factura_id, albaran["recepcion_id"], empresa_id))

        conn.commit()
        conn.close()
        if abs(diferencia) > 0.01:
            flash('Factura creada, pero queda pendiente de validar por diferencia con albaranes.', 'warning')
        else:
            flash('Factura de proveedor creada y albaranes marcados como facturados.', 'success')
        return redirect(url_for('contabilidad.ver_documento', documento_id=factura_id))

    proveedor_id = _to_int_or_none(request.args.get('proveedor_id'))
    proveedores = conn.execute("""
        SELECT id, nombre
        FROM proveedores
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY nombre
    """, (empresa_id,)).fetchall() if _table_exists(conn, "proveedores") else []
    albaranes = []
    if proveedor_id:
        scope_sql, scope_params = _scope_contabilidad_documentos("d", conn, empresa_id)
        albaranes = conn.execute(f"""
            SELECT d.*, t.nombre AS tercero_nombre, r.id AS recepcion_id, r.numero_albaran
            FROM contabilidad_documentos d
            LEFT JOIN contabilidad_terceros t ON t.id = d.tercero_id
            LEFT JOIN recepciones r
                   ON r.contabilidad_documento_id = d.id
                  AND r.empresa_id = d.empresa_id
            WHERE {scope_sql}
              AND d.proveedor_id = ?
              AND d.tipo = 'albaran'
              AND d.estado = 'pendiente_factura'
            ORDER BY date(COALESCE(d.fecha_documento, d.fecha_creacion)), d.id
        """, scope_params + [proveedor_id]).fetchall()
    opciones = _opciones_base(conn, empresa_id)
    conn.close()
    return render_template(
        'contabilidad/factura_proveedor_form.html',
        proveedores=proveedores,
        proveedor_id=proveedor_id,
        albaranes=albaranes,
        opciones=opciones,
        now=datetime.now(),
    )


@contabilidad_bp.route('/sincronizar-ventas', methods=['POST'])
@login_required
def sincronizar_ventas():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    creados = 0
    actualizados = 0

    if _table_exists(conn, "recepcion_facturas"):
        centro_id = centro_activo_id()
        facturas_join = ""
        facturas_filtro = ""
        facturas_params = [empresa_id]
        if centro_id:
            if _table_exists(conn, "reservas_recepcion"):
                facturas_join = """
                LEFT JOIN reservas_recepcion rr
                       ON rr.id = f.reserva_id
                      AND rr.empresa_id = f.empresa_id
                """
                facturas_filtro = "AND rr.centro_id = ?"
                facturas_params.append(centro_id)
            else:
                facturas_filtro = "AND 1 = 0"
        facturas = conn.execute(f"""
            SELECT f.*
            FROM recepcion_facturas f
            {facturas_join}
            WHERE f.empresa_id = ?
              AND COALESCE(f.estado, 'emitida') NOT IN ('anulada', 'cancelada')
              AND COALESCE(f.tipo, 'factura') <> 'proforma'
              AND COALESCE(f.total, 0) <> 0
              {facturas_filtro}
            ORDER BY date(COALESCE(f.fecha, f.fecha_creacion)), f.id
        """, facturas_params).fetchall()
        for factura in facturas:
            centro_doc_id = centro_activo_id()
            if not centro_doc_id and _table_exists(conn, "reservas_recepcion") and factura["reserva_id"]:
                row_centro = conn.execute(
                    "SELECT centro_id FROM reservas_recepcion WHERE id = ? AND empresa_id = ?",
                    (factura["reserva_id"], empresa_id),
                ).fetchone()
                centro_doc_id = row_centro["centro_id"] if row_centro and row_centro["centro_id"] else None
            total = _to_float(factura["total"], 0.0)
            pagado = min(_to_float(factura["pagado"], 0.0), total)
            saldo = _to_float(factura["saldo"], max(total - pagado, 0.0))
            estado = _estado_documento_venta(factura["estado"], total, pagado, saldo)
            tercero_id = _ensure_tercero_cliente(
                conn,
                factura["cliente_nombre"],
                factura["cliente_nif"],
                factura["cliente_email"] if "cliente_email" in factura.keys() else "",
                empresa_id,
            )
            existente = conn.execute("""
                SELECT id
                FROM contabilidad_documentos
                WHERE empresa_id = ?
                  AND origen_modulo = 'recepcion'
                  AND origen_tipo = 'factura'
                  AND origen_id = ?
            """, (empresa_id, factura["id"])).fetchone()
            if existente:
                documento_id = existente["id"]
                conn.execute("""
                    UPDATE contabilidad_documentos
                    SET numero = ?, tercero_id = ?, fecha_documento = ?, estado = ?,
                        base_imponible = ?, impuesto_importe = ?, total = ?,
                        pagado = ?, pendiente = ?, reserva_id = ?, cuenta_contable = '705',
                        centro_id = ?
                    WHERE id = ? AND empresa_id = ?
                """, (
                    factura["numero_completo"],
                    tercero_id,
                    factura["fecha"],
                    estado,
                    round(_to_float(factura["base"], 0.0), 2),
                    round(_to_float(factura["impuestos"], 0.0), 2),
                    round(total, 2),
                    round(pagado, 2),
                    round(max(total - pagado, 0.0), 2),
                    factura["reserva_id"],
                    centro_doc_id,
                    documento_id,
                    empresa_id,
                ))
                actualizados += 1
            else:
                cur = conn.execute("""
                    INSERT INTO contabilidad_documentos (
                        tipo, numero, tercero_id, fecha_documento, fecha_vencimiento,
                        estado, base_imponible, impuesto_importe, total, pagado, pendiente,
                        cuenta_contable, reserva_id, origen_modulo, origen_tipo, origen_id,
                        observaciones, creado_por, empresa_id, centro_id
                    )
                    VALUES ('factura_venta', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            '705', ?, 'recepcion', 'factura', ?, ?, ?, ?, ?)
                """, (
                    factura["numero_completo"] or f"REC-{factura['id']}",
                    tercero_id,
                    factura["fecha"],
                    factura["fecha"],
                    estado,
                    round(_to_float(factura["base"], 0.0), 2),
                    round(_to_float(factura["impuestos"], 0.0), 2),
                    round(total, 2),
                    round(pagado, 2),
                    round(max(total - pagado, 0.0), 2),
                    factura["reserva_id"],
                    factura["id"],
                    factura["observaciones"] if "observaciones" in factura.keys() else None,
                    session.get('user_nombre') or 'admin',
                    empresa_id,
                    centro_doc_id,
                ))
                documento_id = cur.lastrowid
                lineas = conn.execute("""
                    SELECT *
                    FROM recepcion_factura_lineas
                    WHERE factura_id = ? AND empresa_id = ?
                    ORDER BY orden, id
                """, (factura["id"], empresa_id)).fetchall() if _table_exists(conn, "recepcion_factura_lineas") else []
                if lineas:
                    for linea in lineas:
                        conn.execute("""
                            INSERT INTO contabilidad_lineas_documento
                            (documento_id, concepto, cantidad, precio_unitario, base,
                             impuesto_tipo, impuesto_importe, total, cuenta_contable, empresa_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '705', ?)
                        """, (
                            documento_id,
                            linea["concepto"],
                            _to_float(linea["cantidad"], 1.0),
                            _to_float(linea["precio_unitario"], 0.0),
                            _to_float(linea["base"], 0.0),
                            _to_float(linea["impuesto_pct"], 0.0),
                            _to_float(linea["impuesto"], 0.0),
                            _to_float(linea["total"], 0.0),
                            empresa_id,
                        ))
                else:
                    conn.execute("""
                        INSERT INTO contabilidad_lineas_documento
                        (documento_id, concepto, cantidad, precio_unitario, base,
                         impuesto_tipo, impuesto_importe, total, cuenta_contable, empresa_id)
                        VALUES (?, 'Factura recepcion', 1, ?, ?, 0, ?, ?, '705', ?)
                    """, (
                        documento_id,
                        round(total, 2),
                        round(_to_float(factura["base"], total), 2),
                        round(_to_float(factura["impuestos"], 0.0), 2),
                        round(total, 2),
                        empresa_id,
                    ))
                creados += 1

            pagado_doc = conn.execute("""
                SELECT COALESCE(SUM(importe), 0) AS total
                FROM contabilidad_pagos
                WHERE documento_id = ? AND empresa_id = ?
                  AND COALESCE(metodo, '') = 'recepcion'
            """, (documento_id, empresa_id)).fetchone()["total"]
            diferencia_pago = round(pagado - _to_float(pagado_doc, 0.0), 2)
            if diferencia_pago > 0.009:
                conn.execute("""
                    INSERT INTO contabilidad_pagos
                    (documento_id, fecha_pago, metodo, importe, cuenta_tesoreria, referencia, usuario, empresa_id)
                    VALUES (?, ?, 'recepcion', ?, '570', ?, ?, ?)
                """, (
                    documento_id,
                    factura["fecha"] or datetime.now().strftime("%Y-%m-%d"),
                    diferencia_pago,
                    factura["numero_completo"],
                    session.get('user_nombre') or 'admin',
                    empresa_id,
                ))

    if _table_exists(conn, "tpv_cuentas"):
        centro_id = centro_activo_id()
        filtro_pv = "AND pv.centro_id = ?" if centro_id else ""
        params_cuentas = [empresa_id] + ([centro_id] if centro_id else [])
        cuentas = conn.execute(f"""
            SELECT c.*, pv.nombre AS punto_venta_nombre
            FROM tpv_cuentas c
            LEFT JOIN puntos_venta pv ON pv.id = c.punto_venta_id
            WHERE c.empresa_id = ?
              AND COALESCE(c.estado, '') = 'cobrada'
              AND COALESCE(c.total, 0) > 0
              AND COALESCE(c.cargo_habitacion, 0) = 0
              AND LOWER(COALESCE(c.forma_pago, '')) <> 'cargo_habitacion'
              {filtro_pv}
            ORDER BY datetime(COALESCE(c.fecha_cierre, c.fecha_apertura)), c.id
        """, params_cuentas).fetchall()
        for cuenta in cuentas:
            centro_doc_id = _centro_desde_departamento_o_pv(conn, None, cuenta["punto_venta_id"])
            existente = conn.execute("""
                SELECT id
                FROM contabilidad_documentos
                WHERE empresa_id = ?
                  AND origen_modulo = 'tpv'
                  AND origen_tipo = 'cuenta'
                  AND origen_id = ?
            """, (empresa_id, cuenta["id"])).fetchone()
            total = _to_float(cuenta["total"], 0.0)
            metodo = cuenta["forma_pago"] or "efectivo"
            tercero_id = _ensure_tercero_cliente(
                conn,
                cuenta["cliente"] or "Cliente TPV contado",
                "",
                "",
                empresa_id,
                codigo_base="430TPV",
            )
            fecha_doc = (cuenta["fecha_cierre"] or cuenta["fecha_apertura"] or datetime.now().strftime("%Y-%m-%d"))[:10]
            if existente:
                documento_id = existente["id"]
                conn.execute("""
                    UPDATE contabilidad_documentos
                    SET numero = ?, tercero_id = ?, fecha_documento = ?, estado = 'pagado',
                        base_imponible = ?, impuesto_importe = 0, total = ?,
                        pagado = ?, pendiente = 0, punto_venta_id = ?, cuenta_contable = '700',
                        centro_id = ?
                    WHERE id = ? AND empresa_id = ?
                """, (
                    cuenta["numero"] or f"TPV-{cuenta['id']}",
                    tercero_id,
                    fecha_doc,
                    round(total, 2),
                    round(total, 2),
                    round(total, 2),
                    cuenta["punto_venta_id"],
                    centro_doc_id,
                    documento_id,
                    empresa_id,
                ))
                actualizados += 1
            else:
                cur = conn.execute("""
                    INSERT INTO contabilidad_documentos (
                        tipo, numero, tercero_id, fecha_documento, fecha_vencimiento,
                        estado, base_imponible, impuesto_importe, total, pagado, pendiente,
                        cuenta_contable, punto_venta_id, reserva_id, origen_modulo, origen_tipo,
                        origen_id, observaciones, creado_por, empresa_id, centro_id
                    )
                    VALUES ('ticket_venta', ?, ?, ?, ?, 'pagado', ?, 0, ?, ?, 0,
                            '700', ?, ?, 'tpv', 'cuenta', ?, ?, ?, ?, ?)
                """, (
                    cuenta["numero"] or f"TPV-{cuenta['id']}",
                    tercero_id,
                    fecha_doc,
                    fecha_doc,
                    round(total, 2),
                    round(total, 2),
                    round(total, 2),
                    cuenta["punto_venta_id"],
                    cuenta["reserva_id"],
                    cuenta["id"],
                    cuenta["observaciones"],
                    session.get('user_nombre') or 'admin',
                    empresa_id,
                    centro_doc_id,
                ))
                documento_id = cur.lastrowid
                lineas = conn.execute("""
                    SELECT l.*
                    FROM tpv_comanda_lineas l
                    JOIN tpv_comandas c ON c.id = l.comanda_id
                    WHERE c.cuenta_id = ? AND c.empresa_id = ?
                    ORDER BY l.id
                """, (cuenta["id"], empresa_id)).fetchall() if _table_exists(conn, "tpv_comanda_lineas") and _table_exists(conn, "tpv_comandas") else []
                if lineas:
                    for linea in lineas:
                        linea_total = _to_float(linea["total"], 0.0)
                        conn.execute("""
                            INSERT INTO contabilidad_lineas_documento
                            (documento_id, concepto, cantidad, precio_unitario, base,
                             impuesto_tipo, impuesto_importe, total, cuenta_contable,
                             punto_venta_id, empresa_id)
                            VALUES (?, ?, ?, ?, ?, 0, 0, ?, '700', ?, ?)
                        """, (
                            documento_id,
                            linea["nombre"] if "nombre" in linea.keys() else "Articulo TPV",
                            _to_float(linea["cantidad"], 1.0),
                            _to_float(linea["precio"], 0.0),
                            round(linea_total, 2),
                            round(linea_total, 2),
                            cuenta["punto_venta_id"],
                            empresa_id,
                        ))
                else:
                    conn.execute("""
                        INSERT INTO contabilidad_lineas_documento
                        (documento_id, concepto, cantidad, precio_unitario, base,
                         impuesto_tipo, impuesto_importe, total, cuenta_contable, punto_venta_id, empresa_id)
                        VALUES (?, ?, 1, ?, ?, 0, 0, ?, '700', ?, ?)
                    """, (
                        documento_id,
                        cuenta["mesa_nombre"] or cuenta["punto_venta_nombre"] or "Ticket TPV",
                        round(total, 2),
                        round(total, 2),
                        round(total, 2),
                        cuenta["punto_venta_id"],
                        empresa_id,
                    ))
                conn.execute("""
                    INSERT INTO contabilidad_pagos
                    (documento_id, fecha_pago, metodo, importe, cuenta_tesoreria, referencia, usuario, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    documento_id,
                    fecha_doc,
                    metodo,
                    round(total, 2),
                    _cuenta_tesoreria(metodo),
                    cuenta["numero"] or f"TPV-{cuenta['id']}",
                    session.get('user_nombre') or 'admin',
                    empresa_id,
                ))
                creados += 1

    conn.commit()
    conn.close()
    flash(f'Ventas sincronizadas. Creados: {creados}. Actualizados: {actualizados}.', 'success')
    return redirect(url_for('contabilidad.documentos', tipo='factura_venta'))


@contabilidad_bp.route('/pagos')
@login_required
def pagos():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    desde = request.args.get('desde') or datetime.now().strftime("%Y-%m-01")
    hasta = request.args.get('hasta') or datetime.now().strftime("%Y-%m-%d")
    scope_sql, scope_params = _scope_contabilidad_documentos("d", conn, empresa_id)
    rows = conn.execute(f"""
        SELECT p.*, d.numero, d.tipo, d.estado, t.nombre AS tercero_nombre
        FROM contabilidad_pagos p
        JOIN contabilidad_documentos d ON d.id = p.documento_id
        LEFT JOIN contabilidad_terceros t ON t.id = d.tercero_id
        WHERE {scope_sql}
          AND date(p.fecha_pago) >= date(?)
          AND date(p.fecha_pago) <= date(?)
        ORDER BY date(p.fecha_pago) DESC, p.id DESC
        LIMIT 500
    """, scope_params + [desde, hasta]).fetchall()
    resumen = conn.execute(f"""
        SELECT metodo, cuenta_tesoreria, COUNT(*) AS movimientos, COALESCE(SUM(importe), 0) AS total
        FROM contabilidad_pagos p
        JOIN contabilidad_documentos d ON d.id = p.documento_id
        WHERE {scope_sql}
          AND date(p.fecha_pago) >= date(?)
          AND date(p.fecha_pago) <= date(?)
        GROUP BY metodo, cuenta_tesoreria
        ORDER BY total DESC
    """, scope_params + [desde, hasta]).fetchall()
    total = conn.execute(f"""
        SELECT COALESCE(SUM(importe), 0) AS total
        FROM contabilidad_pagos p
        JOIN contabilidad_documentos d ON d.id = p.documento_id
        WHERE {scope_sql}
          AND date(p.fecha_pago) >= date(?)
          AND date(p.fecha_pago) <= date(?)
    """, scope_params + [desde, hasta]).fetchone()["total"]
    conn.close()
    return render_template(
        'contabilidad/pagos.html',
        pagos=rows,
        resumen=resumen,
        total=total,
        filtros={"desde": desde, "hasta": hasta},
    )


@contabilidad_bp.route('/impuestos', methods=['GET', 'POST'])
@login_required
def impuestos():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    if request.method == 'POST':
        impuesto_id = _to_int_or_none(request.form.get('impuesto_id'))
        datos = (
            (request.form.get('nombre') or '').strip(),
            (request.form.get('tipo') or 'IVA').strip().upper(),
            _to_float(request.form.get('porcentaje'), 0.0),
            (request.form.get('territorio') or 'ES').strip().upper(),
            request.form.get('fecha_inicio') or None,
            request.form.get('fecha_fin') or None,
            1 if request.form.get('activo') else 0,
        )
        if not datos[0]:
            flash('El impuesto necesita nombre.', 'warning')
        elif impuesto_id:
            conn.execute("""
                UPDATE contabilidad_impuestos
                SET nombre = ?, tipo = ?, porcentaje = ?, territorio = ?,
                    fecha_inicio = ?, fecha_fin = ?, activo = ?
                WHERE id = ? AND empresa_id = ?
            """, datos + (impuesto_id, empresa_id))
            conn.commit()
            flash('Impuesto actualizado.', 'success')
        else:
            conn.execute("""
                INSERT INTO contabilidad_impuestos
                (nombre, tipo, porcentaje, territorio, fecha_inicio, fecha_fin, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, datos + (empresa_id,))
            conn.commit()
            flash('Impuesto creado.', 'success')
        conn.close()
        return redirect(url_for('contabilidad.impuestos'))
    rows = conn.execute("""
        SELECT *
        FROM contabilidad_impuestos
        WHERE empresa_id = ?
        ORDER BY activo DESC, territorio, tipo, porcentaje DESC
    """, (empresa_id,)).fetchall()
    conn.close()
    return render_template('contabilidad/impuestos.html', impuestos=rows)


@contabilidad_bp.route('/recurrentes', methods=['GET', 'POST'])
@login_required
def recurrentes():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    if request.method == 'POST':
        proveedor_id = _to_int_or_none(request.form.get('proveedor_id'))
        tercero_id = _to_int_or_none(request.form.get('tercero_id'))
        if proveedor_id and not tercero_id:
            tercero_id = _ensure_tercero_proveedor(conn, proveedor_id, empresa_id)
        base = _to_float(request.form.get('base_imponible'), 0.0)
        impuesto_pct = _to_float(request.form.get('impuesto_pct'), 0.0)
        impuesto = _to_float(request.form.get('impuesto_importe'), round(base * impuesto_pct / 100, 2))
        total = _to_float(request.form.get('total'), round(base + impuesto, 2))
        nombre = (request.form.get('nombre') or '').strip()
        if not nombre or total <= 0:
            flash('El gasto recurrente necesita nombre e importe.', 'warning')
        else:
            departamento_id = _to_int_or_none(request.form.get('departamento_id'))
            punto_venta_id = _to_int_or_none(request.form.get('punto_venta_id'))
            centro_doc_id = _centro_desde_departamento_o_pv(conn, departamento_id, punto_venta_id)
            conn.execute("""
                INSERT INTO contabilidad_recurrentes (
                    nombre, tipo_documento, tercero_id, proveedor_id, cuenta_contable,
                    base_imponible, impuesto_pct, impuesto_importe, total, frecuencia,
                    dia_mes, siguiente_fecha, centro_coste, departamento_id, subdepartamento_id,
                    punto_venta_id, observaciones, activo, empresa_id, centro_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """, (
                nombre,
                request.form.get('tipo_documento') or 'gasto',
                tercero_id,
                proveedor_id,
                request.form.get('cuenta_contable') or '629',
                round(base, 2),
                round(impuesto_pct, 2),
                round(impuesto, 2),
                round(total, 2),
                request.form.get('frecuencia') or 'mensual',
                _to_int_or_none(request.form.get('dia_mes')) or 1,
                request.form.get('siguiente_fecha') or datetime.now().strftime("%Y-%m-%d"),
                request.form.get('centro_coste') or None,
                departamento_id,
                _to_int_or_none(request.form.get('subdepartamento_id')),
                punto_venta_id,
                request.form.get('observaciones') or None,
                empresa_id,
                centro_doc_id,
            ))
            conn.commit()
            flash('Gasto recurrente creado.', 'success')
        conn.close()
        return redirect(url_for('contabilidad.recurrentes'))

    scope_sql, scope_params = _scope_contabilidad_recurrentes("r", conn, empresa_id)
    rows = conn.execute(f"""
        SELECT r.*, t.nombre AS tercero_nombre, p.nombre AS proveedor_nombre,
               c.nombre AS cuenta_nombre
        FROM contabilidad_recurrentes r
        LEFT JOIN contabilidad_terceros t ON t.id = r.tercero_id
        LEFT JOIN proveedores p ON p.id = r.proveedor_id
        LEFT JOIN contabilidad_cuentas c ON c.codigo = r.cuenta_contable AND c.empresa_id = r.empresa_id
        WHERE {scope_sql}
        ORDER BY r.activo DESC, date(COALESCE(r.siguiente_fecha, '2999-12-31')), r.nombre
    """, scope_params).fetchall()
    opciones = _opciones_base(conn, empresa_id)
    conn.close()
    return render_template(
        'contabilidad/recurrentes.html',
        recurrentes=rows,
        opciones=opciones,
        tipos=TIPOS_DOCUMENTO,
        now=datetime.now(),
    )


@contabilidad_bp.route('/recurrentes/<int:recurrente_id>/generar', methods=['POST'])
@login_required
def generar_recurrente(recurrente_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    scope_sql, scope_params = _scope_contabilidad_recurrentes("contabilidad_recurrentes", conn, empresa_id)
    recurrente = conn.execute(f"""
        SELECT *
        FROM contabilidad_recurrentes
        WHERE id = ? AND {scope_sql} AND COALESCE(activo, 1) = 1
    """, (recurrente_id, *scope_params)).fetchone()
    if not recurrente:
        conn.close()
        flash('Gasto recurrente no encontrado.', 'warning')
        return redirect(url_for('contabilidad.recurrentes'))

    fecha_doc = request.form.get('fecha_documento') or recurrente["siguiente_fecha"] or datetime.now().strftime("%Y-%m-%d")
    existente = conn.execute("""
        SELECT id
        FROM contabilidad_documentos
        WHERE empresa_id = ?
          AND origen_modulo = 'contabilidad'
          AND origen_tipo = 'recurrente'
          AND origen_id = ?
          AND fecha_documento = ?
          AND (? IS NULL OR centro_id = ?)
        LIMIT 1
    """, (empresa_id, recurrente_id, fecha_doc, recurrente["centro_id"], recurrente["centro_id"])).fetchone()
    if existente:
        conn.close()
        flash('Ese recurrente ya tiene documento generado para esa fecha.', 'warning')
        return redirect(url_for('contabilidad.ver_documento', documento_id=existente["id"]))

    tipo = recurrente["tipo_documento"] or 'gasto'
    numero = request.form.get('numero') or f"REC-{recurrente_id}-{fecha_doc[:7]}"
    pendiente = _to_float(recurrente["total"], 0.0)
    cur = conn.execute("""
        INSERT INTO contabilidad_documentos (
            tipo, numero, tercero_id, proveedor_id, fecha_documento, fecha_vencimiento,
            estado, base_imponible, impuesto_importe, total, pagado, pendiente,
            cuenta_contable, centro_coste, departamento_id, subdepartamento_id,
            punto_venta_id, origen_modulo, origen_tipo, origen_id, observaciones,
            creado_por, empresa_id, centro_id
        )
        VALUES (?, ?, ?, ?, ?, ?, 'pendiente_pago', ?, ?, ?, 0, ?,
                ?, ?, ?, ?, ?, 'contabilidad', 'recurrente', ?, ?, ?, ?, ?)
    """, (
        tipo,
        numero,
        recurrente["tercero_id"],
        recurrente["proveedor_id"],
        fecha_doc,
        request.form.get('fecha_vencimiento') or fecha_doc,
        round(_to_float(recurrente["base_imponible"], 0.0), 2),
        round(_to_float(recurrente["impuesto_importe"], 0.0), 2),
        round(_to_float(recurrente["total"], 0.0), 2),
        round(pendiente, 2),
        recurrente["cuenta_contable"] or '629',
        recurrente["centro_coste"],
        recurrente["departamento_id"],
        recurrente["subdepartamento_id"],
        recurrente["punto_venta_id"],
        recurrente_id,
        recurrente["observaciones"],
        session.get('user_nombre') or 'admin',
        empresa_id,
        recurrente["centro_id"],
    ))
    documento_id = cur.lastrowid
    conn.execute("""
        INSERT INTO contabilidad_lineas_documento
        (documento_id, concepto, cantidad, precio_unitario, base, impuesto_tipo,
         impuesto_importe, total, cuenta_contable, departamento_id, subdepartamento_id,
         punto_venta_id, empresa_id)
        VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        documento_id,
        recurrente["nombre"],
        round(_to_float(recurrente["base_imponible"], 0.0), 2),
        round(_to_float(recurrente["base_imponible"], 0.0), 2),
        round(_to_float(recurrente["impuesto_pct"], 0.0), 2),
        round(_to_float(recurrente["impuesto_importe"], 0.0), 2),
        round(_to_float(recurrente["total"], 0.0), 2),
        recurrente["cuenta_contable"] or '629',
        recurrente["departamento_id"],
        recurrente["subdepartamento_id"],
        recurrente["punto_venta_id"],
        empresa_id,
    ))
    conn.execute("""
        UPDATE contabilidad_recurrentes
        SET siguiente_fecha = ?
        WHERE id = ? AND empresa_id = ?
    """, (_siguiente_fecha_recurrente(fecha_doc, recurrente["frecuencia"]), recurrente_id, empresa_id))
    conn.commit()
    conn.close()
    flash('Documento recurrente generado.', 'success')
    return redirect(url_for('contabilidad.ver_documento', documento_id=documento_id))


@contabilidad_bp.route('/explotacion')
@login_required
def explotacion():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_schema(conn)
    conn.commit()
    mes = (request.args.get('mes') or datetime.now().strftime("%Y-%m"))[:7]
    try:
        inicio_dt = datetime.strptime(f"{mes}-01", "%Y-%m-%d")
    except Exception:
        inicio_dt = datetime.now().replace(day=1)
        mes = inicio_dt.strftime("%Y-%m")
    inicio = inicio_dt.strftime("%Y-%m-%d")
    siguiente = _add_months(inicio_dt, 1).strftime("%Y-%m-%d")
    scope_sql, scope_params = _scope_contabilidad_documentos("d", conn, empresa_id)
    where_base = """
        {scope_sql}
        AND date(COALESCE(d.fecha_documento, d.fecha_creacion)) >= date(?)
        AND date(COALESCE(d.fecha_documento, d.fecha_creacion)) < date(?)
        AND d.estado NOT IN ('cancelado', 'facturado')
    """.format(scope_sql=scope_sql)
    base_params = scope_params + [inicio, siguiente]
    resumen = conn.execute(f"""
        SELECT
            COALESCE(SUM(CASE WHEN d.tipo IN ('factura_venta', 'ticket_venta') THEN d.total ELSE 0 END), 0) AS ingresos,
            COALESCE(SUM(CASE WHEN d.tipo NOT IN ('factura_venta', 'ticket_venta') THEN d.total ELSE 0 END), 0) AS gastos,
            COALESCE(SUM(CASE WHEN d.estado IN ('pendiente_pago', 'pagado_parcial', 'vencido', 'bloqueado') THEN d.pendiente ELSE 0 END), 0) AS pendiente,
            COUNT(*) AS documentos
        FROM contabilidad_documentos d
        WHERE {where_base}
    """, base_params).fetchone()
    pagos = conn.execute(f"""
        SELECT COALESCE(SUM(importe), 0) AS total
        FROM contabilidad_pagos p
        JOIN contabilidad_documentos d ON d.id = p.documento_id
        WHERE {scope_sql}
          AND date(p.fecha_pago) >= date(?)
          AND date(p.fecha_pago) < date(?)
    """, scope_params + [inicio, siguiente]).fetchone()
    por_cuenta = conn.execute(f"""
        SELECT COALESCE(d.cuenta_contable, 'SIN') AS cuenta,
               COALESCE(c.nombre, 'Sin cuenta') AS cuenta_nombre,
               COALESCE(SUM(CASE WHEN d.tipo IN ('factura_venta', 'ticket_venta') THEN d.total ELSE 0 END), 0) AS ingresos,
               COALESCE(SUM(CASE WHEN d.tipo NOT IN ('factura_venta', 'ticket_venta') THEN d.total ELSE 0 END), 0) AS gastos,
               COUNT(*) AS documentos
        FROM contabilidad_documentos d
        LEFT JOIN contabilidad_cuentas c ON c.codigo = d.cuenta_contable AND c.empresa_id = d.empresa_id
        WHERE {where_base}
        GROUP BY COALESCE(d.cuenta_contable, 'SIN'), COALESCE(c.nombre, 'Sin cuenta')
        ORDER BY gastos DESC, ingresos DESC
    """, base_params).fetchall()
    por_tipo = conn.execute(f"""
        SELECT d.tipo,
               COUNT(*) AS documentos,
               COALESCE(SUM(d.total), 0) AS total,
               COALESCE(SUM(d.pendiente), 0) AS pendiente
        FROM contabilidad_documentos d
        WHERE {where_base}
        GROUP BY d.tipo
        ORDER BY total DESC
    """, base_params).fetchall()
    scope_rec_sql, scope_rec_params = _scope_contabilidad_recurrentes("r", conn, empresa_id)
    recurrentes_pendientes = conn.execute(f"""
        SELECT r.*, t.nombre AS tercero_nombre
        FROM contabilidad_recurrentes r
        LEFT JOIN contabilidad_terceros t ON t.id = r.tercero_id
        WHERE {scope_rec_sql}
          AND COALESCE(r.activo, 1) = 1
          AND date(COALESCE(r.siguiente_fecha, '2999-12-31')) < date(?)
        ORDER BY date(r.siguiente_fecha), r.nombre
        LIMIT 20
    """, scope_rec_params + [siguiente]).fetchall()
    conn.close()
    ingresos = _to_float(resumen["ingresos"], 0.0)
    gastos = _to_float(resumen["gastos"], 0.0)
    return render_template(
        'contabilidad/explotacion.html',
        mes=mes,
        resumen=resumen,
        pagos=pagos,
        resultado=round(ingresos - gastos, 2),
        por_cuenta=por_cuenta,
        por_tipo=por_tipo,
        recurrentes_pendientes=recurrentes_pendientes,
    )

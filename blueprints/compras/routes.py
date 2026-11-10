# blueprints/compras/routes.py
from flask import render_template, request, redirect, url_for, session, flash, make_response
from database import get_db
from datetime import datetime, timedelta
from . import compras_bp
from blueprints.auth.routes import (
    departamento_scope_sql,
    login_required,
    usuario_departamento_id,
    usuario_es_jefatura_departamento,
    usuario_es_global_operacion,
    usuario_puede_ver_departamento,
    usuario_tiene_permiso,
)
import pandas as pd
from io import BytesIO
from flask import send_file
from flask import current_app
import uuid
import os
from werkzeug.utils import secure_filename
from flask import send_file
from pathlib import Path
import re
import math
import unicodedata
from typing import Any
from helpers.operativa_helper import ensure_incidencias_context_schema
from helpers.etiquetas_helper import build_label_css, get_current_print_device, get_label_config, get_label_configs
from helpers.label_raw_helper import zpl_etiquetas_recepcion
from helpers.ambito_helper import filtro_ambito_operativo
from blueprints.proveedores.routes import proveedores_select

# ============================================
# FUNCIONES AUXILIARES
# ============================================

DOCUMENTOS_COMPRA_EXT_PERMITIDAS = {'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff'}
DOCUMENTOS_COMPRA_MIME_PERMITIDOS = {
    'application/pdf',
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/tiff',
}


def _columnas_tabla(cursor, tabla):
    try:
        return [row[1] for row in cursor.execute(f"PRAGMA table_info({tabla})").fetchall()]
    except Exception:
        return []


def _safe_add_column(cursor, tabla, definicion):
    nombre = definicion.strip().split()[0]
    if nombre not in _columnas_tabla(cursor, tabla):
        cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN {definicion}")


def _table_exists(conn, table):
    try:
        return bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone())
    except Exception:
        return False


def _estado_facturacion_compra(tipo_documento):
    tipo = (tipo_documento or "albaran").strip().lower()
    if tipo in {"factura", "ticket", "factura_simplificada"}:
        return "factura_recibida"
    return "pendiente_factura"


def _fecha_vencimiento_compra(fecha_base, dias_pago):
    fecha_txt = str(fecha_base or datetime.now().strftime("%Y-%m-%d"))[:10]
    try:
        fecha = datetime.fromisoformat(fecha_txt)
    except Exception:
        fecha = datetime.now()
    try:
        dias = int(dias_pago or 0)
    except Exception:
        dias = 0
    return (fecha + timedelta(days=max(dias, 0))).strftime("%Y-%m-%d")


def _asegurar_estructura_compras(cursor):
    for tabla in ('departamentos', 'subdepartamentos', 'almacenes', 'puntos_venta', 'proveedores'):
        _safe_add_column(cursor, tabla, 'centro_id INTEGER')
    _safe_add_column(cursor, 'proveedores', 'email_pedidos TEXT')
    """Migración defensiva de compras. No falla si algo ya existe."""
    ensure_incidencias_context_schema(cursor.connection)
    _safe_add_column(cursor, 'pedidos', 'departamento_id INTEGER')
    _safe_add_column(cursor, 'pedidos', 'departamento_solicitante_id INTEGER')
    _safe_add_column(cursor, 'pedidos', 'subdepartamento_solicitante_id INTEGER')
    _safe_add_column(cursor, 'pedidos', 'punto_venta_id INTEGER')
    _safe_add_column(cursor, 'pedidos', 'tipo_pedido TEXT')
    _safe_add_column(cursor, 'pedidos', 'estado_recepcion TEXT')
    _safe_add_column(cursor, 'pedidos', 'fecha_recepcion DATE')
    _safe_add_column(cursor, 'pedidos', 'fecha_envio DATETIME')
    _safe_add_column(cursor, 'pedidos', 'cerrado INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'pedidos', 'fecha_cierre DATETIME')
    _safe_add_column(cursor, 'pedidos', 'solicitado_por TEXT')
    _safe_add_column(cursor, 'pedidos', 'fecha_solicitud_autorizacion DATETIME')
    _safe_add_column(cursor, 'pedidos', 'aprobado_por TEXT')
    _safe_add_column(cursor, 'pedidos', 'fecha_aprobacion DATETIME')
    _safe_add_column(cursor, 'pedidos', 'rechazado_por TEXT')
    _safe_add_column(cursor, 'pedidos', 'fecha_rechazo DATETIME')
    _safe_add_column(cursor, 'pedidos', 'motivo_rechazo TEXT')
    _safe_add_column(cursor, 'pedidos', 'motivo_cancelacion TEXT')
    _safe_add_column(cursor, 'recepciones', 'departamento_id INTEGER')
    _safe_add_column(cursor, 'recepciones', 'departamento_receptor_id INTEGER')
    _safe_add_column(cursor, 'recepciones', 'subdepartamento_receptor_id INTEGER')
    _safe_add_column(cursor, 'recepciones', 'punto_venta_id INTEGER')
    _safe_add_column(cursor, 'recepciones', 'tipo_recepcion TEXT')
    _safe_add_column(cursor, 'recepciones', 'validada_operativamente INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'recepciones', 'validada_economicamente INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'recepciones', 'stock_aplicado INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'recepciones', 'documento_adjunto_id INTEGER')
    _safe_add_column(cursor, 'recepciones', 'documento_economico_tipo TEXT')
    _safe_add_column(cursor, 'recepciones', 'estado_facturacion TEXT')
    _safe_add_column(cursor, 'recepciones', 'fecha_vencimiento_economica DATE')
    _safe_add_column(cursor, 'recepciones', 'forma_pago_economica TEXT')
    _safe_add_column(cursor, 'recepciones', 'contabilidad_documento_id INTEGER')
    _safe_add_column(cursor, 'proveedores', 'documento_habitual TEXT DEFAULT "albaran"')
    _safe_add_column(cursor, 'proveedores', 'facturacion_habitual TEXT DEFAULT "mensual"')
    _safe_add_column(cursor, 'proveedores', 'forma_pago TEXT DEFAULT "transferencia"')
    _safe_add_column(cursor, 'proveedores', 'dias_pago INTEGER DEFAULT 30')
    _safe_add_column(cursor, 'lineas_recepcion', 'almacen_id INTEGER')
    _safe_add_column(cursor, 'lineas_recepcion', 'lote TEXT')
    _safe_add_column(cursor, 'lineas_recepcion', 'fecha_caducidad DATE')
    _safe_add_column(cursor, 'lineas_recepcion', 'temperatura REAL')
    _safe_add_column(cursor, 'lineas_recepcion', 'precio_unitario REAL')
    _safe_add_column(cursor, 'lineas_recepcion', 'importe_total REAL')
    _safe_add_column(cursor, 'lineas_recepcion', 'num_envases INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'lineas_recepcion', 'lote_id INTEGER')
    _safe_add_column(cursor, 'lineas_recepcion', 'aceptado INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'lineas_recepcion', 'motivo_rechazo TEXT')
    _safe_add_column(cursor, 'lineas_recepcion', 'incidencia_id INTEGER')
    _safe_add_column(cursor, 'lineas_recepcion', 'empresa_id INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'lotes', 'responsable_recepcion TEXT')
    _safe_add_column(cursor, 'lotes', 'incidencia_id INTEGER')
    _safe_add_column(cursor, 'lotes', 'documento_origen TEXT')
    _safe_add_column(cursor, 'lotes', 'notas_trazabilidad TEXT')
    _safe_add_column(cursor, 'lotes', 'origen_lote TEXT DEFAULT "proveedor"')
    _safe_add_column(cursor, 'lotes', 'coste_unitario REAL DEFAULT 0')
    _safe_add_column(cursor, 'lotes', 'coste_total REAL DEFAULT 0')
    _safe_add_column(cursor, 'lotes', 'moneda TEXT DEFAULT "EUR"')
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movimientos_lotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            tipo TEXT NOT NULL,
            cantidad REAL NOT NULL,
            destino_tipo TEXT,
            destino_id INTEGER,
            usuario TEXT,
            observaciones TEXT,
            documento TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (lote_id) REFERENCES lotes(id)
        )
    """)
    _safe_add_column(cursor, 'movimientos_lotes', 'documento TEXT')
    _safe_add_column(cursor, 'movimientos_lotes', 'empresa_id INTEGER DEFAULT 1')
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registros_desinfeccion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            producto_usado TEXT,
            dosis TEXT,
            tiempo_contacto TEXT,
            aclarado INTEGER DEFAULT 1,
            resultado TEXT,
            responsable TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (lote_id) REFERENCES lotes(id)
        )
    """)
    _safe_add_column(cursor, 'registros_desinfeccion', 'empresa_id INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'productos', 'visible_en_compras INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'productos', 'visible_en_produccion INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'productos', 'visible_en_venta INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'productos', 'permite_compra_directa INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'productos', 'centro_id INTEGER')
    _safe_add_column(cursor, 'productos', 'departamento_responsable_id INTEGER')
    _safe_add_column(cursor, 'productos', 'almacen_preferente_id INTEGER')
    _safe_add_column(cursor, 'productos', 'codigo_interno TEXT')
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS productos_proveedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER,
            proveedor_id INTEGER,
            precio REAL,
            activo INTEGER DEFAULT 1
        )
    """)
    _safe_add_column(cursor, 'productos_proveedores', 'proveedor_id INTEGER')
    _safe_add_column(cursor, 'productos_proveedores', 'codigo_proveedor TEXT')
    _safe_add_column(cursor, 'productos_proveedores', 'referencia_proveedor TEXT')
    _safe_add_column(cursor, 'productos_proveedores', 'precio REAL')
    _safe_add_column(cursor, 'productos_proveedores', 'precio_compra REAL')
    _safe_add_column(cursor, 'productos_proveedores', 'precio_ultimo REAL')
    _safe_add_column(cursor, 'productos_proveedores', 'precio_vigente REAL')
    _safe_add_column(cursor, 'productos_proveedores', 'precio_medio REAL')
    _safe_add_column(cursor, 'productos_proveedores', 'activo INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'productos_proveedores', 'preferente INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'productos_proveedores', 'es_principal INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'productos_proveedores', 'fecha_actualizacion DATE DEFAULT CURRENT_DATE')
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documentos_compra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor_id INTEGER,
            pedido_id INTEGER,
            recepcion_id INTEGER,
            tipo_documento TEXT DEFAULT 'albaran',
            nombre_archivo TEXT,
            ruta_archivo TEXT,
            mime_type TEXT,
            origen_subida TEXT,
            texto_extraido TEXT,
            procesado_ia INTEGER DEFAULT 0,
            validado INTEGER DEFAULT 0,
            fecha_subida DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        _safe_add_column(cursor, 'documentos_compra', 'empresa_id INTEGER DEFAULT 1')
        _safe_add_column(cursor, 'documentos_compra', 'numero_documento TEXT')
        _safe_add_column(cursor, 'documentos_compra', 'fecha_documento TEXT')
    except Exception:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documento_compra_lineas_detectadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento_id INTEGER,
            producto_id INTEGER,
            descripcion_detectada TEXT,
            cantidad_detectada REAL,
            precio_detectado REAL,
            estado TEXT,
            observaciones TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recepcion_validaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recepcion_id INTEGER NOT NULL,
            tipo_validacion TEXT,
            estado TEXT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            observaciones TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS productos_aliases_compra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            proveedor_id INTEGER,
            producto_id INTEGER NOT NULL,
            referencia TEXT,
            descripcion TEXT,
            descripcion_normalizada TEXT,
            activo INTEGER DEFAULT 1,
            veces INTEGER DEFAULT 1,
            fecha_ultima DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS compras_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            tipo TEXT NOT NULL,
            referencia_tipo TEXT,
            referencia_id INTEGER,
            usuario TEXT,
            departamento_id INTEGER,
            proveedor_id INTEGER,
            estado_anterior TEXT,
            estado_nuevo TEXT,
            detalle TEXT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS compras_historico_precios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            producto_id INTEGER NOT NULL,
            proveedor_id INTEGER,
            precio_anterior REAL,
            precio_nuevo REAL NOT NULL,
            origen TEXT,
            referencia_tipo TEXT,
            referencia_id INTEGER,
            usuario TEXT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _departamentos_activos(conn, empresa_id):
    """Devuelve departamentos disponibles sin dejar vacío el selector por datos antiguos.

    Intenta primero departamentos activos de la empresa, luego globales o cualquier
    departamento activo, y finalmente cualquier registro existente.
    """
    consultas = [
        ("""
            SELECT id, nombre
            FROM departamentos
            WHERE COALESCE(activo, 1) = 1
              AND (empresa_id = ? OR empresa_id IS NULL)
            ORDER BY nombre
        """, (empresa_id,)),
        ("""
            SELECT id, nombre
            FROM departamentos
            WHERE COALESCE(activo, 1) = 1
            ORDER BY nombre
        """, ()),
        ("""
            SELECT id, nombre
            FROM departamentos
            WHERE empresa_id = ? OR empresa_id IS NULL
            ORDER BY nombre
        """, (empresa_id,)),
        ("""
            SELECT id, nombre
            FROM departamentos
            ORDER BY nombre
        """, ()),
    ]
    for sql, params in consultas:
        try:
            rows = conn.execute(sql, params).fetchall()
            if rows:
                return rows
        except Exception:
            continue
    return []


ROLES_COMPRAS_GLOBAL = {
    'compras',
    'economato',
    'almacen',
    'jefe_compras',
    'responsable_compras',
    'contable',
    'contabilidad',
    'administracion',
}


def _empresa_id():
    return session.get('user_empresa_id', 1) or 1


def _scope_departamento(alias, empresa_id=None):
    empresa_id = empresa_id or _empresa_id()
    tabla = {
        "p": "pedidos",
        "pedidos": "pedidos",
        "r": "recepciones",
        "recepciones": "recepciones",
        "a": "compras_auditoria",
        "compras_auditoria": "compras_auditoria",
    }.get(alias, alias)
    if tabla in {"pedidos", "recepciones"}:
        conn = get_db()
        try:
            return filtro_ambito_operativo(
                conn,
                alias,
                empresa_id,
                tabla,
                departamento_col="departamento_solicitante_id" if tabla == "pedidos" else "departamento_receptor_id",
                subdepartamento_col="subdepartamento_solicitante_id" if tabla == "pedidos" else "subdepartamento_receptor_id",
                punto_venta_col="punto_venta_id",
                extra_roles=ROLES_COMPRAS_GLOBAL,
            )
        finally:
            conn.close()
    return departamento_scope_sql(
        alias,
        empresa_id,
        extra_roles=ROLES_COMPRAS_GLOBAL,
    )


def _centro_activo_id():
    valor = session.get("centro_activo_id")
    try:
        return int(valor) if valor else None
    except Exception:
        return None


def _ids_centro(conn, empresa_id, centro_id):
    departamentos = conn.execute(
        """
        SELECT id
        FROM departamentos
        WHERE empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1
        """,
        (empresa_id, centro_id),
    ).fetchall()
    departamento_ids = [row["id"] for row in departamentos]
    punto_venta_ids = []
    if "centro_id" in _columnas_tabla(conn.cursor(), "puntos_venta"):
        punto_venta_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM puntos_venta WHERE empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1",
                (empresa_id, centro_id),
            ).fetchall()
        ]
    return departamento_ids, punto_venta_ids


def _scope_pedidos_centro(conn, alias, empresa_id):
    scope_sql, scope_params = _scope_departamento(alias, empresa_id)
    centro_id = _centro_activo_id()
    if not centro_id:
        return scope_sql, scope_params, None
    if usuario_es_global_operacion(ROLES_COMPRAS_GLOBAL) and "1 = 0" in scope_sql:
        scope_sql, scope_params = f"{alias}.empresa_id = ?", [empresa_id]

    departamento_ids, punto_venta_ids = _ids_centro(conn, empresa_id, centro_id)
    pedido_cols = _columnas_tabla(conn.cursor(), "pedidos")
    condiciones = []
    params = []
    if departamento_ids and "departamento_id" in pedido_cols:
        condiciones.append(f"{alias}.departamento_id IN ({','.join('?' for _ in departamento_ids)})")
        params.extend(departamento_ids)
    if departamento_ids and "departamento_solicitante_id" in pedido_cols:
        condiciones.append(f"{alias}.departamento_solicitante_id IN ({','.join('?' for _ in departamento_ids)})")
        params.extend(departamento_ids)
    if punto_venta_ids and "punto_venta_id" in pedido_cols:
        condiciones.append(f"{alias}.punto_venta_id IN ({','.join('?' for _ in punto_venta_ids)})")
        params.extend(punto_venta_ids)

    if not condiciones:
        return f"{scope_sql} AND 1 = 0", scope_params, centro_id

    return f"{scope_sql} AND ({' OR '.join(condiciones)})", scope_params + params, centro_id


def _scope_recepciones_centro(conn, alias, empresa_id):
    scope_sql, scope_params = _scope_departamento(alias, empresa_id)
    centro_id = _centro_activo_id()
    if not centro_id:
        return scope_sql, scope_params, None
    if usuario_es_global_operacion(ROLES_COMPRAS_GLOBAL) and "1 = 0" in scope_sql:
        scope_sql, scope_params = f"{alias}.empresa_id = ?", [empresa_id]

    departamento_ids, punto_venta_ids = _ids_centro(conn, empresa_id, centro_id)
    recepcion_cols = _columnas_tabla(conn.cursor(), "recepciones")
    condiciones = []
    params = []
    for columna in ("departamento_id", "departamento_receptor_id"):
        if departamento_ids and columna in recepcion_cols:
            condiciones.append(f"{alias}.{columna} IN ({','.join('?' for _ in departamento_ids)})")
            params.extend(departamento_ids)
    if punto_venta_ids and "punto_venta_id" in recepcion_cols:
        condiciones.append(f"{alias}.punto_venta_id IN ({','.join('?' for _ in punto_venta_ids)})")
        params.extend(punto_venta_ids)

    if not condiciones:
        return f"{scope_sql} AND 1 = 0", scope_params, centro_id

    return f"{scope_sql} AND ({' OR '.join(condiciones)})", scope_params + params, centro_id


def _departamentos_visibles_compras(conn, empresa_id):
    centro_id = _centro_activo_id()
    centro_sql = ""
    centro_params = []
    if centro_id:
        centro_sql = " AND centro_id = ?"
        centro_params.append(centro_id)

    if usuario_es_global_operacion(ROLES_COMPRAS_GLOBAL):
        if centro_id:
            return conn.execute(
                f"""
                SELECT id, nombre
                FROM departamentos
                WHERE COALESCE(activo, 1) = 1
                  AND (empresa_id = ? OR empresa_id IS NULL)
                  {centro_sql}
                ORDER BY nombre
                """,
                [empresa_id] + centro_params,
            ).fetchall()
        return _departamentos_activos(conn, empresa_id)

    departamento_id = usuario_departamento_id()
    if not departamento_id:
        return []

    return conn.execute(f"""
        SELECT id, nombre
        FROM departamentos
        WHERE id = ?
          AND COALESCE(activo, 1) = 1
          AND (empresa_id = ? OR empresa_id IS NULL)
          {centro_sql}
        ORDER BY nombre
    """, [departamento_id, empresa_id] + centro_params).fetchall()


def _normalizar_nombre_departamento(nombre):
    texto = str(nombre or '').strip().lower()
    traducciones = str.maketrans('áéíóúüñ', 'aeiouun')
    return texto.translate(traducciones)


def _mapa_departamentos_compra(conn, empresa_id):
    rows = conn.execute("""
        SELECT id, nombre
        FROM departamentos
        WHERE COALESCE(activo, 1) = 1
          AND (empresa_id = ? OR empresa_id IS NULL)
    """, (empresa_id,)).fetchall()
    return {
        _normalizar_nombre_departamento(row['nombre']): int(row['id'])
        for row in rows
    }


def _departamento_por_nombre(mapa_departamentos, *nombres):
    for nombre in nombres:
        dep_id = mapa_departamentos.get(_normalizar_nombre_departamento(nombre))
        if dep_id:
            return dep_id
    return None


def _resolver_departamento_producto_compra(
    mapa_departamentos,
    producto_departamento_id=None,
    almacen_departamento_id=None,
    familia='',
    nombre='',
    fallback_id=None,
):
    if producto_departamento_id:
        return int(producto_departamento_id)

    texto = f"{familia or ''} {nombre or ''}".lower()
    if any(palabra in texto for palabra in ('bebida', 'bar', 'snack', 'helado', 'cafeteria', 'cafe', 'cerveza', 'vino', 'refresco')):
        dep_id = _departamento_por_nombre(mapa_departamentos, 'BARES')
        if dep_id:
            return dep_id
    if any(palabra in texto for palabra in ('limpieza', 'lavanderia', 'amenit', 'toalla', 'sabana', 'papel higienico')):
        dep_id = _departamento_por_nombre(mapa_departamentos, 'Pisos')
        if dep_id:
            return dep_id
    if any(palabra in texto for palabra in ('mantenimiento', 'recambio', 'bombilla', 'filtro', 'pila', 'sifon', 'silicona', 'latiguillo')):
        dep_id = _departamento_por_nombre(mapa_departamentos, 'Mantenimiento')
        if dep_id:
            return dep_id
    if any(palabra in texto for palabra in ('toner', 'tinta impresora recepcion')):
        dep_id = _departamento_por_nombre(mapa_departamentos, 'Recepción', 'RECEPCION')
        if dep_id:
            return dep_id
    if any(palabra in texto for palabra in ('papeleria', 'folio', 'boligrafo', 'grapa', 'carpeta', 'oficina')):
        dep_id = _departamento_por_nombre(mapa_departamentos, 'ADMINISTRACION')
        if dep_id:
            return dep_id

    if almacen_departamento_id:
        return int(almacen_departamento_id)
    if fallback_id:
        return int(fallback_id)
    return None


def _departamento_del_centro_activo(departamento_id):
    centro_id = _centro_activo_id()
    if not centro_id or not departamento_id:
        return False
    conn = get_db()
    try:
        if 'centro_id' not in _columnas_tabla(conn.cursor(), 'departamentos'):
            return False
        departamento = conn.execute('''
            SELECT id FROM departamentos
            WHERE id = ? AND empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1
        ''', (departamento_id, _empresa_id(), centro_id)).fetchone()
        return bool(departamento)
    finally:
        conn.close()

def _departamento_form_permitido(departamento_id):
    return _departamento_del_centro_activo(departamento_id) and usuario_puede_gestionar_compras_departamento(departamento_id)


def _departamento_recepcion_form_permitido(departamento_id):
    return _departamento_del_centro_activo(departamento_id) and usuario_puede_recepcionar_compras_departamento(departamento_id)


def usuario_puede_gestionar_compras_departamento(departamento_id=None):
    if usuario_es_global_operacion(ROLES_COMPRAS_GLOBAL):
        return True
    if not usuario_es_jefatura_departamento():
        return False
    if not departamento_id:
        return bool(usuario_departamento_id())
    return usuario_puede_ver_departamento(departamento_id, ROLES_COMPRAS_GLOBAL)


def usuario_puede_recepcionar_compras_departamento(departamento_id=None):
    if usuario_es_global_operacion(ROLES_COMPRAS_GLOBAL):
        return True

    tiene_permiso_recepcion = (
        usuario_tiene_permiso('compras_recepcionar')
        or usuario_tiene_permiso('compras_gestionar')
        or usuario_tiene_permiso('compras_autorizar_departamento')
    )
    if not tiene_permiso_recepcion:
        return False

    departamento_usuario = usuario_departamento_id()
    if not departamento_id:
        return True
    if not departamento_usuario:
        return True
    return usuario_puede_ver_departamento(departamento_id, ROLES_COMPRAS_GLOBAL)


def _requiere_gestion_compras(conn, departamento_id=None, destino='compras.lista_pedidos'):
    if usuario_puede_gestionar_compras_departamento(departamento_id):
        return None
    conn.close()
    flash('Solo el jefe o segundo jefe del departamento puede hacer pedidos y compras.', 'warning')
    return redirect(url_for(destino))


def _requiere_recepcion_compras(conn, departamento_id=None, destino='compras.validar_recepciones'):
    if usuario_puede_recepcionar_compras_departamento(departamento_id):
        return None
    conn.close()
    flash('No tienes permiso para recepcionar mercancia de este departamento.', 'warning')
    return redirect(url_for(destino))


def _almacenes_visibles_compras(conn, empresa_id, departamento_id=None):
    scope_sql, scope_params = _scope_departamento('a', empresa_id)
    params = list(scope_params)
    filtro_departamento = ""
    departamento_filtrado = _to_int_or_none(departamento_id)
    if departamento_filtrado:
        filtro_departamento = " AND a.departamento_id = ?"
        params.append(departamento_filtrado)
    return conn.execute(f"""
        SELECT id, nombre, departamento_id
        FROM almacenes a
        WHERE COALESCE(a.activo, 1) = 1
          AND {scope_sql}
          {filtro_departamento}
        ORDER BY a.nombre
    """, params).fetchall()


def _to_float_safe(valor: Any, defecto: Any = 0.0):
    try:
        txt = str(valor if valor is not None else '').strip().replace(',', '.')
        return float(txt) if txt else defecto
    except Exception:
        return defecto


def _to_int_or_none(valor):
    try:
        txt = str(valor if valor is not None else '').strip()
        return int(txt) if txt else None
    except Exception:
        return None


def _resumen_lineas_pedido(cursor, pedido_id):
    row = cursor.execute("""
        SELECT COUNT(*) AS total_lineas,
               COALESCE(SUM(COALESCE(cantidad_solicitada, 0)), 0) AS total_cantidad,
               SUM(CASE WHEN COALESCE(cantidad_solicitada, 0) <= 0 THEN 1 ELSE 0 END) AS lineas_sin_cantidad,
               SUM(CASE WHEN precio_unitario IS NULL OR COALESCE(precio_unitario, 0) <= 0 THEN 1 ELSE 0 END) AS lineas_sin_precio
        FROM lineas_pedido
        WHERE pedido_id = ?
    """, (pedido_id,)).fetchone()
    return {
        'total_lineas': int(row['total_lineas'] or 0) if row else 0,
        'total_cantidad': _to_float_safe(row['total_cantidad'] if row else 0, 0.0),
        'lineas_sin_cantidad': int(row['lineas_sin_cantidad'] or 0) if row else 0,
        'lineas_sin_precio': int(row['lineas_sin_precio'] or 0) if row else 0,
    }


def _pedido_preparado_para_autorizacion(cursor, pedido_id):
    resumen = _resumen_lineas_pedido(cursor, pedido_id)
    if resumen['total_lineas'] <= 0:
        return False, 'El pedido no tiene lineas. Anade al menos un producto antes de enviarlo a autorizacion.'
    if resumen['lineas_sin_cantidad'] > 0 or resumen['total_cantidad'] <= 0:
        return False, 'Hay lineas sin cantidad valida. Revisa el pedido antes de solicitar autorizacion.'
    return True, ''


def _usuario_actual_compras():
    return (session.get('user_nombre') or session.get('username') or 'admin').strip()


def _normalizar_texto_match(valor):
    texto = str(valor or '').strip().lower()
    texto = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode('ascii')
    return " ".join(re.findall(r'[a-z0-9]+', texto))


def _usuario_puede_ver_precios_compras():
    return (
        usuario_tiene_permiso('ver_precios')
        or usuario_tiene_permiso('compras_validar_economico')
        or usuario_tiene_permiso('compras_gestionar')
        or usuario_tiene_permiso('modificar_precios')
    )


def _producto_proveedor_compra_valido(conn, empresa_id, producto_id, proveedor_id, centro_id=None):
    """Valida proveedor/precio de compra sin fiarse de los campos ocultos del formulario."""
    columnas_productos = _columnas_tabla(conn.cursor(), "productos")
    columnas_proveedores = _columnas_tabla(conn.cursor(), "proveedores")
    producto_centro_sql = ""
    producto_centro_params = []
    proveedor_centro_sql = ""
    proveedor_centro_params = []
    if centro_id and "centro_id" in columnas_productos:
        producto_centro_sql = " AND (centro_id IS NULL OR centro_id = ?)"
        producto_centro_params.append(centro_id)
    if centro_id and "centro_id" in columnas_proveedores:
        proveedor_centro_sql = " AND (centro_id IS NULL OR centro_id = ?)"
        proveedor_centro_params.append(centro_id)

    producto = conn.execute("""
        SELECT id, precio, proveedor_id
        FROM productos
        WHERE id = ?
          AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND COALESCE(visible_en_compras, 1) = 1
          AND COALESCE(permite_compra_directa, 1) = 1
          """ + producto_centro_sql + """
    """, [producto_id, empresa_id] + producto_centro_params).fetchone()
    if not producto:
        return None

    proveedor = conn.execute("""
        SELECT id
        FROM proveedores
        WHERE id = ?
          AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          """ + proveedor_centro_sql + """
    """, [proveedor_id, empresa_id] + proveedor_centro_params).fetchone()
    if not proveedor:
        return None

    relacion = conn.execute("""
        SELECT id,
               COALESCE(precio_vigente, precio_ultimo, precio_compra, precio, ?) AS precio_referencia
        FROM productos_proveedores
        WHERE producto_id = ?
          AND proveedor_id = ?
          AND COALESCE(activo, 1) = 1
        ORDER BY COALESCE(es_principal, 0) DESC,
                 COALESCE(preferente, 0) DESC,
                 id DESC
        LIMIT 1
    """, (producto['precio'] or 0, producto_id, proveedor_id)).fetchone()
    if relacion:
        return {
            'proveedor_id': proveedor_id,
            'precio': _to_float_safe(relacion['precio_referencia'], 0.0),
            'origen': 'producto_proveedor',
        }

    if producto['proveedor_id'] and int(producto['proveedor_id']) == int(proveedor_id):
        return {
            'proveedor_id': proveedor_id,
            'precio': _to_float_safe(producto['precio'], 0.0),
            'origen': 'proveedor_producto',
        }

    return None


def _obtener_nombre_empresa(conn, empresa_id):
    """Devuelve nombre del establecimiento/empresa de forma defensiva.

    El proyecto ha tenido distintas tablas según versión. Esta función no rompe
    si una tabla/columna no existe; usa el primer nombre válido que encuentre.
    """
    candidatos = [
        ('empresas', ['nombre_comercial', 'nombre', 'razon_social']),
        ('empresa', ['nombre_comercial', 'nombre', 'razon_social']),
        ('configuracion_empresa', ['nombre_comercial', 'nombre_empresa', 'nombre']),
        ('configuracion', ['nombre_comercial', 'nombre_empresa', 'empresa_nombre', 'nombre']),
    ]

    cur = conn.cursor()
    for tabla, columnas_posibles in candidatos:
        try:
            columnas = _columnas_tabla(cur, tabla)
        except Exception:
            columnas = []
        if not columnas:
            continue

        columna_nombre = next((c for c in columnas_posibles if c in columnas), None)
        if not columna_nombre:
            continue

        try:
            if 'id' in columnas:
                row = conn.execute(
                    f"SELECT {columna_nombre} AS nombre FROM {tabla} WHERE id = ? LIMIT 1",
                    (empresa_id,)
                ).fetchone()
                if row and row['nombre']:
                    return str(row['nombre']).strip()
        except Exception:
            pass

        try:
            if 'empresa_id' in columnas:
                row = conn.execute(
                    f"SELECT {columna_nombre} AS nombre FROM {tabla} WHERE empresa_id = ? LIMIT 1",
                    (empresa_id,)
                ).fetchone()
                if row and row['nombre']:
                    return str(row['nombre']).strip()
        except Exception:
            pass

        try:
            row = conn.execute(f"SELECT {columna_nombre} AS nombre FROM {tabla} LIMIT 1").fetchone()
            if row and row['nombre']:
                return str(row['nombre']).strip()
        except Exception:
            pass

    return current_app.config.get('NOMBRE_EMPRESA') or current_app.config.get('APP_NAME') or 'Mi establecimiento'


def _asegurar_config_mail_desde_env():
    """Carga configuración SMTP desde .env/app.config sin depender de import circular."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    for clave in ['MAIL_SERVER', 'MAIL_USERNAME', 'MAIL_PASSWORD', 'MAIL_DEFAULT_SENDER']:
        valor = os.environ.get(clave)
        if valor and not current_app.config.get(clave):
            current_app.config[clave] = valor

    mail_port = os.environ.get('MAIL_PORT')
    if mail_port and not current_app.config.get('MAIL_PORT'):
        try:
            current_app.config['MAIL_PORT'] = int(mail_port)
        except Exception:
            pass

    for clave_bool in ['MAIL_USE_TLS', 'MAIL_USE_SSL']:
        valor = os.environ.get(clave_bool)
        if valor is not None and current_app.config.get(clave_bool) is None:
            current_app.config[clave_bool] = str(valor).strip().lower() in ('1', 'true', 'yes', 'si', 'sí')

    if current_app.config.get('MAIL_USE_TLS') is None:
        current_app.config['MAIL_USE_TLS'] = True
    if current_app.config.get('MAIL_USE_SSL') is None:
        current_app.config['MAIL_USE_SSL'] = False

    remitente = (
        current_app.config.get('MAIL_DEFAULT_SENDER')
        or current_app.config.get('MAIL_USERNAME')
        or os.environ.get('MAIL_DEFAULT_SENDER')
        or os.environ.get('MAIL_USERNAME')
        or ''
    )
    if remitente and not current_app.config.get('MAIL_DEFAULT_SENDER'):
        current_app.config['MAIL_DEFAULT_SENDER'] = remitente

    return remitente


def _obtener_mail_actual():
    """Devuelve la extensión Flask-Mail existente o inicializa una localmente."""
    from flask_mail import Mail
    _asegurar_config_mail_desde_env()
    extension_mail = current_app.extensions.get('mail')
    if extension_mail:
        return extension_mail
    return Mail(current_app)

def generar_numero_pedido():
    """Genera un número de pedido único"""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    fecha = datetime.now().strftime("%Y%m%d")
    
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total FROM pedidos WHERE fecha_pedido = date('now') AND empresa_id = ?", (empresa_id,))
    total_hoy = cursor.fetchone()['total'] + 1
    conn.close()
    
    return f"PED-{fecha}-{total_hoy:03d}"



def _recalcular_estado_pedido(cursor, pedido_id, empresa_id):
    """
    Recalcula el estado del pedido según lo realmente recibido.
    Estados usados:
    - borrador
    - enviado
    - recibido_parcial
    - recibido
    - cerrado
    - cancelado
    """
    pedido = cursor.execute(
        "SELECT estado FROM pedidos WHERE id = ? AND empresa_id = ?",
        (pedido_id, empresa_id)
    ).fetchone()

    if not pedido:
        return None

    # No tocar estados finales administrativos
    if pedido['estado'] in ('cerrado', 'cancelado'):
        return pedido['estado']

    lineas = cursor.execute("""
        SELECT COALESCE(cantidad_solicitada, 0) AS cantidad_solicitada,
               COALESCE(cantidad_recibida, 0) AS cantidad_recibida
        FROM lineas_pedido
        WHERE pedido_id = ?
    """, (pedido_id,)).fetchall()

    if not lineas:
        return pedido['estado']

    total_lineas = len(lineas)
    lineas_con_recepcion = 0
    lineas_completas = 0

    for linea in lineas:
        pedida = float(linea['cantidad_solicitada'] or 0)
        recibida = float(linea['cantidad_recibida'] or 0)

        if recibida > 0:
            lineas_con_recepcion += 1
        if pedida > 0 and recibida >= pedida:
            lineas_completas += 1

    if lineas_completas == total_lineas:
        nuevo_estado = 'recibido'
    elif lineas_con_recepcion > 0:
        nuevo_estado = 'recibido_parcial'
    else:
        # Si aún no hay recepción, solo puede seguir en borrador o enviado
        nuevo_estado = 'enviado' if pedido['estado'] != 'borrador' else 'borrador'

    if nuevo_estado != pedido['estado']:
        cursor.execute(
            "UPDATE pedidos SET estado = ? WHERE id = ? AND empresa_id = ?",
            (nuevo_estado, pedido_id, empresa_id)
        )
    return nuevo_estado


def _pedido_tiene_recepciones(cursor, pedido_id):
    row = cursor.execute(
        "SELECT COUNT(*) AS total FROM recepciones WHERE pedido_id = ?",
        (pedido_id,)
    ).fetchone()
    return bool(row and row['total'])

def guardar_documento_compra_en_cursor(
    cursor,
    file,
    proveedor_id=None,
    pedido_id=None,
    recepcion_id=None,
    tipo_documento='albaran',
    origen_subida='manual'
):
    if not file or file.filename == '':
        return None

    nombre_original = secure_filename(str(file.filename or 'documento'))
    extension = os.path.splitext(nombre_original)[1].lower()
    if extension not in DOCUMENTOS_COMPRA_EXT_PERMITIDAS:
        raise ValueError("Formato de documento no permitido. Usa PDF o imagen.")

    mime_type = (file.mimetype or '').lower()
    if mime_type and mime_type not in DOCUMENTOS_COMPRA_MIME_PERMITIDOS:
        raise ValueError("Tipo de archivo no permitido. Usa PDF o imagen.")

    nombre_archivo = f"{uuid.uuid4().hex}{extension}"
    empresa_id = session.get('user_empresa_id', 1) or 1

    upload_dir = os.path.join(current_app.instance_path, 'uploads', 'compras')
    os.makedirs(upload_dir, exist_ok=True)

    ruta_final = os.path.join(upload_dir, nombre_archivo)
    file.save(ruta_final)

    cursor.execute("""
        INSERT INTO documentos_compra (
            proveedor_id, pedido_id, recepcion_id, tipo_documento,
            nombre_archivo, ruta_archivo, mime_type, origen_subida,
            procesado_ia, validado, empresa_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
    """, (
        proveedor_id, pedido_id, recepcion_id, tipo_documento,
        nombre_archivo, ruta_final, mime_type or file.mimetype, origen_subida,
        empresa_id
    ))

    return cursor.lastrowid




def procesar_documento_compra_ocr_en_cursor(cursor, documento_id, empresa_id, proveedor_id=None):
    """OCR seguro y conservador para documentos de compra.

    Extrae texto, detecta cabecera documental y guarda líneas candidatas.
    No crea productos automáticamente y no mueve stock.
    """
    resumen_vacio = {
        'texto': '',
        'lineas': 0,
        'asociadas': 0,
        'pendientes': 0,
        'numero_pedido': None,
        'numero_albaran': None,
        'numero_documento': None,
        'tipo_documento': None,
        'fecha_documento': None
    }

    if not documento_id:
        return resumen_vacio

    documento = cursor.execute(
        "SELECT * FROM documentos_compra WHERE id = ? AND empresa_id = ?",
        (documento_id, empresa_id)
    ).fetchone()

    if not documento:
        return resumen_vacio
    centro_id = _centro_id_desde_documento_compra(cursor.connection, documento_id, empresa_id)

    texto = extraer_texto_documento_compra(documento['ruta_archivo'])
    cabecera = extraer_cabecera_documento(texto)
    numero_pedido = detectar_numero_pedido_desde_texto(texto)
    numero_documento = cabecera.get('numero')
    tipo_documento = cabecera.get('tipo') or 'albaran'
    fecha_documento = cabecera.get('fecha')

    columnas_doc = _columnas_tabla(cursor, 'documentos_compra')
    set_partes = ["texto_extraido = ?", "procesado_ia = 1"]
    valores = [texto]

    if 'tipo_documento' in columnas_doc and tipo_documento:
        set_partes.append("tipo_documento = ?")
        valores.append(tipo_documento)
    if 'numero_documento' in columnas_doc and numero_documento:
        set_partes.append("numero_documento = ?")
        valores.append(numero_documento)
    if 'fecha_documento' in columnas_doc and fecha_documento:
        set_partes.append("fecha_documento = ?")
        valores.append(fecha_documento)

    valores.append(documento_id)
    cursor.execute(f"""
        UPDATE documentos_compra
        SET {', '.join(set_partes)}
        WHERE id = ?
    """, tuple(valores))

    cursor.execute(
        "DELETE FROM documento_compra_lineas_detectadas WHERE documento_id = ?",
        (documento_id,)
    )

    detectadas = 0
    asociadas = 0
    pendientes = 0

    for linea in detectar_lineas_documento_compra(texto):
        descripcion = (linea.get('descripcion') or '').strip()
        referencia = (linea.get('referencia') or '').strip()

        producto_id = buscar_producto_por_texto(
            cursor.connection,
            descripcion,
            empresa_id,
            referencia=referencia,
            proveedor_id=proveedor_id,
            centro_id=centro_id,
        )

        estado = 'asociado' if producto_id else 'pendiente_producto'
        if producto_id:
            asociadas += 1
        else:
            pendientes += 1

        observaciones = []
        if referencia:
            observaciones.append(f"Ref: {referencia}")
        if linea.get('importe') is not None:
            observaciones.append(f"Importe: {linea.get('importe')}")
        if linea.get('confianza'):
            observaciones.append(f"Confianza: {linea.get('confianza')}")
        if linea.get('linea_original'):
            observaciones.append(f"OCR: {linea.get('linea_original')}")
        if numero_pedido:
            observaciones.append(f"Pedido detectado: {numero_pedido}")
        if numero_documento:
            observaciones.append(f"Documento detectado: {tipo_documento or '-'} {numero_documento}")
        if fecha_documento:
            observaciones.append(f"Fecha doc: {fecha_documento}")

        cursor.execute("""
            INSERT INTO documento_compra_lineas_detectadas (
                documento_id,
                producto_id,
                descripcion_detectada,
                cantidad_detectada,
                precio_detectado,
                estado,
                observaciones
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            documento_id,
            producto_id,
            descripcion,
            linea.get('cantidad'),
            linea.get('precio'),
            estado,
            " | ".join(observaciones)
        ))
        detectadas += 1

    return {
        'texto': texto,
        'lineas': detectadas,
        'asociadas': asociadas,
        'pendientes': pendientes,
        'numero_pedido': numero_pedido,
        'numero_albaran': numero_documento,
        'numero_documento': numero_documento,
        'tipo_documento': tipo_documento,
        'fecha_documento': fecha_documento
    }


def _ocr_tesseract_cmd():
    """Devuelve ruta de Tesseract si existe. No rompe si no está instalado."""
    candidatos = [
        os.environ.get('TESSERACT_CMD'),
        current_app.config.get('TESSERACT_CMD') if current_app else None,
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for ruta in candidatos:
        if ruta and os.path.exists(str(ruta)):
            return str(ruta)
    return None


def _ocr_preparar_imagen(imagen):
    """Preprocesado conservador para tickets/fotos/PDF escaneados."""
    from PIL import ImageOps, ImageFilter, ImageEnhance

    imagen_base = ImageOps.exif_transpose(imagen)
    imagen_base = imagen_base.convert('L')
    imagen_base = ImageOps.autocontrast(imagen_base)

    ancho, alto = imagen_base.size
    if ancho < 1600:
        factor = max(2, int(1600 / max(ancho, 1)))
        imagen_base = imagen_base.resize((ancho * factor, alto * factor))

    imagen_base = ImageEnhance.Contrast(imagen_base).enhance(1.45)
    imagen_base = imagen_base.filter(ImageFilter.SHARPEN)
    return imagen_base


def _ocr_image_to_string_seguro(imagen):
    """Ejecuta Tesseract con varias configuraciones y devuelve el mejor texto."""
    try:
        import pytesseract
    except Exception as e:
        return f'OCR_NO_DISPONIBLE: instala pytesseract. Detalle: {str(e)}'

    cmd = _ocr_tesseract_cmd()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd

    try:
        imagen_base = _ocr_preparar_imagen(imagen)
    except Exception:
        imagen_base = imagen

    textos = []
    for lang in ['spa+eng', 'spa', 'eng']:
        for config in ['--oem 3 --psm 6', '--oem 3 --psm 4', '--oem 3 --psm 11', '--oem 3 --psm 12']:
            try:
                t = pytesseract.image_to_string(imagen_base, lang=lang, config=config)
                if t and t.strip():
                    textos.append(t.strip())
            except Exception:
                continue

    if not textos:
        return 'OCR_SIN_TEXTO: no se pudo leer texto.'

    def _score(txt):
        return (
            len(detectar_lineas_documento_compra(txt)) * 1000
            + len(re.findall(r'\d+[,.]\d+', txt)) * 20
            + len(txt)
        )

    textos.sort(key=_score, reverse=True)
    return textos[0].strip()


def extraer_texto_documento_compra(ruta_archivo):
    """Extrae texto de PDF digital, PDF escaneado e imágenes/fotos."""
    if not ruta_archivo or not os.path.exists(ruta_archivo):
        return ''

    extension = Path(ruta_archivo).suffix.lower()

    if extension == '.pdf':
        try:
            from pypdf import PdfReader
            reader = PdfReader(ruta_archivo)
            textos = []
            for page in reader.pages:
                try:
                    textos.append(page.extract_text() or '')
                except Exception:
                    continue
            texto = '\n'.join(textos).strip()
            if texto and len(texto) >= 25:
                return texto
        except Exception:
            pass

        try:
            import fitz  # PyMuPDF
            from PIL import Image
            doc = fitz.open(ruta_archivo)
            textos_ocr = []
            for page in doc:
                try:
                    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
                    imagen = Image.open(BytesIO(pix.tobytes("png")))
                    texto_pagina = _ocr_image_to_string_seguro(imagen)
                    if texto_pagina and not texto_pagina.startswith('OCR_NO_DISPONIBLE'):
                        textos_ocr.append(texto_pagina)
                except Exception:
                    continue
            texto_final = '\n'.join(textos_ocr).strip()
            if texto_final:
                return texto_final
        except Exception:
            pass

        try:
            from pdf2image import convert_from_path
            poppler_cfg = current_app.config.get('POPPLER_PATH') if current_app else None
            poppler_env = os.environ.get('POPPLER_PATH')
            poppler_path = str(poppler_cfg or poppler_env) if (poppler_cfg or poppler_env) else None

            if poppler_path:
                paginas = convert_from_path(pdf_path=ruta_archivo, dpi=300, poppler_path=poppler_path)
            else:
                paginas = convert_from_path(pdf_path=ruta_archivo, dpi=300)

            textos_ocr = []
            for pagina in paginas:
                texto_pagina = _ocr_image_to_string_seguro(pagina)
                if texto_pagina and not texto_pagina.startswith('OCR_NO_DISPONIBLE'):
                    textos_ocr.append(texto_pagina)

            texto_final = '\n'.join(textos_ocr).strip()
            if texto_final:
                return texto_final

            return 'OCR_SIN_TEXTO: PDF escaneado convertido, pero no se pudo leer texto.'
        except Exception as e:
            return f'PDF_ESCANEADO_SIN_TEXTO: instala/configura PyMuPDF o pdf2image + Poppler para leer este PDF. Detalle: {str(e)}'

    if extension in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.heic', '.heif']:
        try:
            if extension in ['.heic', '.heif']:
                try:
                    import pillow_heif
                    pillow_heif.register_heif_opener()
                except Exception:
                    pass
            from PIL import Image
            imagen = Image.open(ruta_archivo)
            return _ocr_image_to_string_seguro(imagen)
        except Exception as e:
            return f'ERROR_OCR_IMAGEN: {str(e)}'

    return 'TIPO_DOCUMENTO_NO_SOPORTADO'


def _normalizar_importe_ocr(valor):
    if valor is None:
        return None

    texto = str(valor).strip()
    if not texto:
        return None

    texto = texto.replace('€', '').replace('%', '').replace(' ', '')
    texto = texto.replace("'", ',').replace('´', ',').replace('`', ',')
    texto = texto.replace('O', '0').replace('o', '0')

    if '/' in texto and not re.fullmatch(r'\d+/\d{1,3}', texto):
        return None
    texto = texto.replace('/', ',')

    if re.match(r'^\d{1,3}(\.\d{3})+,\d+$', texto):
        texto = texto.replace('.', '').replace(',', '.')
    elif re.match(r'^\d{1,3}(,\d{3})+\.\d+$', texto):
        texto = texto.replace(',', '')
    else:
        texto = texto.replace(',', '.')

    texto = re.sub(r'[^0-9.\-]', '', texto)
    texto = re.sub(r'\.+', '.', texto)
    if texto in ('', '.', '-', '-.'):
        return None

    try:
        return float(texto)
    except Exception:
        return None


def _extraer_total_documento(texto):
    if not texto:
        return None
    candidatos = []
    for linea in str(texto).splitlines():
        limpia = " ".join(linea.strip().split())
        lower = limpia.lower()
        if 'total' in lower or 'entregado' in lower:
            for n in re.findall(r'\d+(?:[.,]\d+)?', limpia):
                valor = _normalizar_importe_ocr(n)
                if valor is not None and 0 < valor < 100000:
                    candidatos.append(valor)
    return candidatos[-1] if candidatos else None


def detectar_numero_pedido_desde_texto(texto):
    """Detecta número de pedido Nexora tipo PED-20260430-005."""
    if not texto:
        return None

    texto_limpio = str(texto).upper().replace('–', '-').replace('—', '-')
    patrones = [
        r'\bPED[-\s]*(\d{8})[-\s]*(\d{3,5})\b',
        r'\bPEDIDO\s*(?:N[ºO°]?\.?\s*)?(PED[-\s]*\d{8}[-\s]*\d{3,5})\b',
        r'\bN[ºO°]?\.?\s*PED[-\s]*(\d{8})[-\s]*(\d{3,5})\b',
    ]

    for patron in patrones:
        m = re.search(patron, texto_limpio, flags=re.I)
        if not m:
            continue
        if len(m.groups()) == 2:
            return f"PED-{m.group(1)}-{m.group(2)}"
        return re.sub(r'\s+', '', m.group(1)).replace('PED', 'PED')

    return None


def detectar_numero_albaran_desde_texto(texto):
    """Detecta Nº documental: albarán/factura/ticket/recibo/presupuesto."""
    return extraer_cabecera_documento(texto).get('numero')


def extraer_cabecera_documento(texto):
    """Extrae tipo, número y fecha del documento."""
    cabecera = {'numero': None, 'fecha': None, 'tipo': None}
    if not texto:
        return cabecera

    texto_str = str(texto).replace('–', '-').replace('—', '-')
    lineas = [" ".join((l or '').replace('\xa0', ' ').split()) for l in texto_str.splitlines()]
    texto_lower = texto_str.lower()

    if re.search(r'factura\s+simplificada', texto_lower, flags=re.I):
        cabecera['tipo'] = 'ticket'
    elif re.search(r'\bfactura\b', texto_lower, flags=re.I):
        cabecera['tipo'] = 'factura'
    elif re.search(r'\balbar[aá]n\b|\balb\.?\b', texto_lower, flags=re.I):
        cabecera['tipo'] = 'albaran'
    elif re.search(r'\bpresupuesto\b', texto_lower, flags=re.I):
        cabecera['tipo'] = 'presupuesto'
    elif re.search(r'\bticket\b|\brecibo\b', texto_lower, flags=re.I):
        cabecera['tipo'] = 'ticket'
    elif re.search(r'\bdocumento\b', texto_lower, flags=re.I):
        cabecera['tipo'] = 'documento'

    def _limpiar_numero(valor):
        valor = (valor or '').strip().strip('.:-#')
        valor = valor.replace('º', '').replace('°', '')
        valor = re.sub(r'\s+', '', valor)
        return valor[:40]

    def _numero_valido(valor):
        c = _limpiar_numero(valor)
        if not c or len(c) < 3:
            return False
        if not re.search(r'\d', c):
            return False
        cu = c.upper()
        if cu.startswith('PED'):
            return False
        if re.fullmatch(r'[A-Z]?\d{7,9}[A-Z]', cu):
            return False
        if re.fullmatch(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', c):
            return False
        solo_digitos = re.sub(r'\D', '', c)
        if len(solo_digitos) >= 9 and re.fullmatch(r'[+\d\s().-]+', c):
            return False
        if re.fullmatch(r'\d{1,2}', c):
            return False
        return True

    for linea in lineas:
        if 'fecha' in linea.lower():
            m_fecha = re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', linea)
            if m_fecha:
                cabecera['fecha'] = m_fecha.group(0)
                break
    if not cabecera['fecha']:
        m_fecha = re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', texto_str)
        if m_fecha:
            cabecera['fecha'] = m_fecha.group(0)

    patrones = [
        r'\bNRO\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\/\-.]{2,30})',
        r'\bN[ºO°]\.?\s*(?:DE\s*)?(?:FACTURA|ALBAR[AÁ]N|DOCUMENTO|PRESUPUESTO|TICKET|RECIBO)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\/\-.]{2,30})',
        r'\bFACTURA\s*(?:N[ºO°]?\.?|NUM(?:ERO)?\.?|NO\.?|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\/\-.]{2,30})',
        r'\bALBAR[AÁ]N\s*(?:N[ºO°]?\.?|NUM(?:ERO)?\.?|NO\.?|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\/\-.]{2,30})',
        r'\bALB\.?\s*(?:N[ºO°]?\.?|NUM(?:ERO)?\.?|NO\.?|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\/\-.]{2,30})',
        r'\bDOCUMENTO\s*(?:N[ºO°]?\.?|NUM(?:ERO)?\.?|NO\.?|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\/\-.]{2,30})',
        r'\bPRESUPUESTO\s*(?:N[ºO°]?\.?|NUM(?:ERO)?\.?|NO\.?|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\/\-.]{2,30})',
        r'\bTICKET\s*(?:N[ºO°]?\.?|NUM(?:ERO)?\.?|NO\.?|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\/\-.]{2,30})',
        r'\bRECIBO\s*(?:N[ºO°]?\.?|NUM(?:ERO)?\.?|NO\.?|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\/\-.]{2,30})',
    ]

    for linea in lineas:
        for patron in patrones:
            m = re.search(patron, linea, flags=re.I)
            if m and _numero_valido(m.group(1)):
                cabecera['numero'] = _limpiar_numero(m.group(1))
                return cabecera

    claves = [
        'n.º de factura', 'nº de factura', 'no de factura', 'numero de factura', 'número de factura',
        'n.º factura', 'nº factura', 'documento caja fecha', 'documento fecha'
    ]
    for i, linea in enumerate(lineas):
        lower = linea.lower()
        if any(k in lower for k in claves):
            for posible in lineas[i + 1:i + 5]:
                candidatos = re.findall(r'\b[A-Z0-9][A-Z0-9\/\-.]{2,30}\b', posible, flags=re.I)
                for candidato in candidatos:
                    if _numero_valido(candidato):
                        cabecera['numero'] = _limpiar_numero(candidato)
                        return cabecera

    return cabecera



def detectar_lineas_documento_compra(texto):
    """Parser OCR conservador para albaranes, facturas, presupuestos y tickets.

    Objetivo: leer líneas reales de producto sin aceptar direcciones, teléfonos,
    CIF/NIF, fechas, totales ni cabeceras. No crea productos.
    """
    import unicodedata

    if not texto:
        return []

    def _sin_acentos(valor):
        return ''.join(c for c in unicodedata.normalize('NFD', str(valor or '')) if unicodedata.category(c) != 'Mn')

    def _lower(valor):
        return _sin_acentos(valor).lower()

    def _limpia(linea):
        linea = (linea or '').replace('\t', ' ').replace('\xa0', ' ').replace('|', ' ')
        linea = re.sub(r'\s+', ' ', linea).strip()
        linea = re.sub(r'^[\-–—_·•\.\s]+', '', linea).strip()
        return linea

    def _tiene_letras(linea):
        return bool(re.search(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', linea or ''))

    def _es_cabecera(linea):
        l = _lower(linea)
        tiene_producto = any(x in l for x in ['descripcion', 'descripción', 'producto', 'articulo', 'artículo', 'concepto'])
        tiene_cantidad = any(x in l for x in ['cantidad', 'cant'])
        tiene_precio = any(x in l for x in ['precio', 'pvp', 'importe', 'total'])
        return tiene_producto and tiene_cantidad and tiene_precio

    def _es_fin(linea):
        l = _lower(linea)
        return bool(re.match(r'^(subtotal|total|base\s+imp|base imponible|iva|igic|imp\.?\s*igic|% rec|observaciones|notas|forma de pago|tarjeta|entregado|cambio|lineas\s*:|líneas\s*:|firma|recibe)\b', l))

    palabras_admin = [
        'proveedor', 'emisor', 'empresa', 'principal', 'direccion', 'dirección',
        'calle', 'avenida', 'avda', 'plaza', 'ciudad', 'provincia', 'codigo postal',
        'código postal', 'cif', 'nif', 'telefono', 'teléfono', 'tel:', 'tlf', 'tfno',
        'email', '@', 'www', 'http', 'fecha', 'factura', 'ticket', 'recibo',
        'albaran', 'albarán', 'presupuesto', 'documento', 'pedido nº', 'pedido no',
        'pedido num', 'pedido n', 'cliente', 'clientes', 'cajero', 'operacion',
        'operación', 'autorizacion', 'autorización', 'vendedor', 'fax', 'seccion',
        'sección', 'obra', 'gracias', 'nombre de la empresa', 'pagar a',
        'fecha de vencimiento'
    ]

    def _tokens(linea):
        res = []
        # Captura cantidades/precios; no captura números pegados a letras por delante.
        patron = r'(?<![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])[-+]?\d+(?:[.,\'´`]\d+)?'
        for m in re.finditer(patron, linea):
            raw = m.group(0)
            val = _normalizar_importe_ocr(raw)
            if val is None:
                continue
            res.append({'raw': raw, 'valor': val, 'start': m.start(), 'end': m.end()})
        return res

    def _administrativa(linea, en_tabla):
        if not linea or len(linea) < 3:
            return True
        l = _lower(linea)
        if _es_cabecera(linea) or _es_fin(linea):
            return True
        if any(p in l for p in palabras_admin):
            return True
        if re.search(r'\bPED[-\s]*\d{8}[-\s]*\d{3,5}\b', linea, flags=re.I):
            return True
        if re.search(r'\b[A-Z]?\d{7,9}[A-Z]\b', linea, flags=re.I):
            return True
        if re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', linea):
            return True
        nums = re.findall(r'\d', linea)
        letras = re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', linea)
        if not en_tabla and len(nums) >= 5 and len(letras) < 16:
            return True
        tk = _tokens(linea)
        if len(tk) < 2:
            return True
        if not en_tabla and len(tk) < 3:
            return True
        return False

    def _limpiar_desc(desc):
        desc = _limpia(desc)
        desc = re.sub(r'^[\-–—_·•\.\s]+', '', desc).strip()
        if desc.startswith('---'):
            desc = desc[3:].strip()
        desc = re.sub(r'[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .,/+\-xX]', ' ', desc)
        desc = re.sub(r'\s+', ' ', desc).strip()
        desc = re.sub(r'\b(kg|kgs|kilo|kilos|unidad|unidades|ud|uds|caja|cajas|bandeja|bandejas|manojos?|manojs?)\b$', '', desc, flags=re.I).strip()
        return desc

    def _codigo_y_nombre(desc):
        desc = _limpiar_desc(desc)
        if not desc:
            return '', ''
        m = re.match(r'^([A-Za-z]{0,8}\d{2,12})([A-Za-zÁÉÍÓÚÜÑáéíóúüñ].+)$', desc)
        if m:
            return m.group(1).strip(), _limpiar_desc(m.group(2))
        partes = desc.split(' ', 1)
        if len(partes) == 2:
            cod, resto = partes[0].strip(), partes[1].strip()
            if cod in ('---', '--', '-'):
                return '', _limpiar_desc(resto)
            if re.match(r'^[A-Za-z]{0,8}\d{1,12}[A-Za-z]{0,4}$', cod) and _tiene_letras(resto):
                return cod, _limpiar_desc(resto)
        return '', desc

    def _item(linea, en_tabla):
        linea = _limpia(linea)
        if _administrativa(linea, en_tabla) or not _tiene_letras(linea):
            return None

        tk = _tokens(linea)
        if len(tk) >= 3:
            cantidad_t, precio_t, importe_t = tk[-3], tk[-2], tk[-1]
        elif en_tabla and len(tk) == 2:
            cantidad_t, precio_t, importe_t = tk[-2], tk[-1], None
        else:
            return None

        cantidad = cantidad_t['valor']
        precio = precio_t['valor'] if precio_t else None
        importe = importe_t['valor'] if importe_t else None

        if cantidad is None or cantidad <= 0 or cantidad > 100000:
            return None
        if precio is not None and (precio <= 0 or precio > 10000):
            return None
        if importe is not None and (importe <= 0 or importe > 1000000):
            return None

        if precio is None and importe is not None and cantidad:
            precio = round(importe / cantidad, 6)

        if precio is not None and importe is not None:
            esperado = cantidad * precio
            if abs(esperado - importe) > max(0.05, abs(importe) * 0.12):
                recalculado = round(importe / cantidad, 6) if cantidad else None
                if recalculado and 0 < recalculado <= 10000:
                    precio = recalculado

        desc_bruta = linea[:cantidad_t['start']].strip()
        referencia, descripcion = _codigo_y_nombre(desc_bruta)
        if len(descripcion) < 3 or not _tiene_letras(descripcion):
            return None

        dl = _lower(descripcion)
        if any(x in dl for x in ['total', 'factura', 'tarjeta', 'cambio', 'base imponible', 'telefono', 'direccion', 'ciudad', 'provincia', 'calle', 'documento']):
            return None

        score = 2
        if referencia:
            score += 2
        if precio is not None:
            score += 1
        if importe is not None:
            score += 1
        if precio is not None and importe is not None and abs(round(cantidad * precio, 2) - round(importe, 2)) <= max(0.05, abs(importe) * 0.05):
            score += 3

        return {
            'referencia': referencia,
            'codigo_proveedor': referencia,
            'descripcion': descripcion,
            'cantidad': round(cantidad, 3),
            'precio': round(precio, 6) if precio is not None else None,
            'importe': round(importe, 2) if importe is not None else None,
            'precio_origen': 'documento_ocr',
            'linea_original': linea,
            'estado_ocr': 'detectado',
            'confianza': 'alta' if score >= 6 else 'media' if score >= 4 else 'baja'
        }

    lineas = [_limpia(l) for l in str(texto).splitlines()]
    inicio = None
    for i, linea in enumerate(lineas):
        if _es_cabecera(linea):
            inicio = i + 1
            break

    candidatas = []
    if inicio is not None:
        for linea in lineas[inicio:]:
            if not linea:
                continue
            if _es_fin(linea):
                break
            candidatas.append((linea, True))
    else:
        candidatas = [(l, False) for l in lineas if l]

    combinadas = []
    i = 0
    while i < len(candidatas):
        actual, en_tabla = candidatas[i]
        if i + 1 < len(candidatas):
            sig, sig_tabla = candidatas[i + 1]
            if _tiene_letras(actual) and len(_tokens(actual)) == 0 and len(_tokens(sig)) >= 2:
                combinadas.append((_limpia(actual + ' ' + sig), en_tabla or sig_tabla))
                i += 2
                continue
        combinadas.append((actual, en_tabla))
        i += 1

    resultado = []
    vistos = set()
    for linea, en_tabla in combinadas:
        it = _item(linea, en_tabla)
        if not it:
            continue
        clave = ((it.get('referencia') or '').lower(), (it.get('descripcion') or '').lower(), it.get('cantidad'), it.get('precio'), it.get('importe'))
        if clave in vistos:
            continue
        vistos.add(clave)
        resultado.append(it)

    return resultado


def _centro_sql_columna(conn, tabla, alias, centro_id, permite_global=True):
    if not centro_id or "centro_id" not in _columnas_tabla(conn.cursor(), tabla):
        return "", []
    if permite_global:
        return f" AND ({alias}.centro_id IS NULL OR {alias}.centro_id = ?)", [centro_id]
    return f" AND {alias}.centro_id = ?", [centro_id]


def _producto_id_en_ambito(conn, empresa_id, producto_id, centro_id=None):
    producto_id = _to_int_or_none(producto_id)
    if not producto_id:
        return None
    centro_id = centro_id or _centro_activo_id()
    producto_scope_sql, producto_scope_params = _centro_sql_columna(conn, "productos", "p", centro_id, permite_global=True)
    row = conn.execute("""
        SELECT p.id
        FROM productos p
        WHERE p.id = ?
          AND p.empresa_id = ?
          AND COALESCE(p.activo, 1) = 1
          """ + producto_scope_sql + """
    """, [producto_id, empresa_id] + producto_scope_params).fetchone()
    return row["id"] if row else None


def _centro_id_desde_documento_compra(conn, documento_id, empresa_id):
    documento = conn.execute("""
        SELECT dc.id,
               r.departamento_id AS recepcion_departamento_id,
               p.departamento_id AS pedido_departamento_id,
               p.departamento_solicitante_id AS pedido_departamento_solicitante_id,
               r.punto_venta_id AS recepcion_punto_venta_id,
               p.punto_venta_id AS pedido_punto_venta_id
        FROM documentos_compra dc
        LEFT JOIN recepciones r ON r.id = dc.recepcion_id AND r.empresa_id = ?
        LEFT JOIN pedidos p ON p.id = COALESCE(dc.pedido_id, r.pedido_id) AND p.empresa_id = ?
        WHERE dc.id = ? AND dc.empresa_id = ?
    """, (empresa_id, empresa_id, documento_id, empresa_id)).fetchone()
    if not documento:
        return None

    for departamento_id in (
        documento["recepcion_departamento_id"],
        documento["pedido_departamento_id"],
        documento["pedido_departamento_solicitante_id"],
    ):
        if departamento_id:
            row = conn.execute("""
                SELECT centro_id
                FROM departamentos
                WHERE id = ? AND (empresa_id = ? OR empresa_id IS NULL)
            """, (departamento_id, empresa_id)).fetchone()
            if row and row["centro_id"]:
                return row["centro_id"]

    for punto_venta_id in (documento["recepcion_punto_venta_id"], documento["pedido_punto_venta_id"]):
        if punto_venta_id:
            row = conn.execute("""
                SELECT centro_id
                FROM puntos_venta
                WHERE id = ? AND empresa_id = ?
            """, (punto_venta_id, empresa_id)).fetchone()
            if row and row["centro_id"]:
                return row["centro_id"]

    return _centro_activo_id()


def buscar_producto_por_texto(conn, descripcion, empresa_id, referencia=None, proveedor_id=None, centro_id=None):
    descripcion = (descripcion or '').strip()
    referencia = (referencia or '').strip()
    descripcion_normalizada = _normalizar_texto_match(descripcion)
    centro_id = centro_id or _centro_activo_id()
    producto_scope_sql, producto_scope_params = _centro_sql_columna(conn, "productos", "p", centro_id, permite_global=True)

    try:
        alias = None
        if referencia:
            alias = conn.execute("""
                SELECT producto_id AS id
                FROM productos_aliases_compra
                WHERE empresa_id = ?
                  AND activo = 1
                  AND referencia = ?
                  AND (proveedor_id = ? OR proveedor_id IS NULL OR ? IS NULL)
                ORDER BY CASE WHEN proveedor_id = ? THEN 0 WHEN proveedor_id IS NULL THEN 1 ELSE 2 END,
                         veces DESC,
                         id DESC
                LIMIT 1
            """, (empresa_id, referencia, proveedor_id, proveedor_id, proveedor_id)).fetchone()
        if not alias and descripcion_normalizada:
            alias = conn.execute("""
                SELECT producto_id AS id
                FROM productos_aliases_compra
                WHERE empresa_id = ?
                  AND activo = 1
                  AND descripcion_normalizada = ?
                  AND (proveedor_id = ? OR proveedor_id IS NULL OR ? IS NULL)
                ORDER BY CASE WHEN proveedor_id = ? THEN 0 WHEN proveedor_id IS NULL THEN 1 ELSE 2 END,
                         veces DESC,
                         id DESC
                LIMIT 1
            """, (empresa_id, descripcion_normalizada, proveedor_id, proveedor_id, proveedor_id)).fetchone()
        if alias:
            validado = _producto_id_en_ambito(conn, empresa_id, alias['id'], centro_id)
            if validado:
                return validado
    except Exception:
        pass

    if referencia:
        for sql, params in [
            ("""
                SELECT producto_id AS id
                FROM productos_proveedores pp
                JOIN productos p ON p.id = pp.producto_id
                WHERE pp.codigo_proveedor = ?
                  AND COALESCE(pp.activo, 1) = 1
                  AND p.empresa_id = ?
                  AND COALESCE(p.activo, 1) = 1
                  AND (pp.proveedor_id = ? OR ? IS NULL)
                  """ + producto_scope_sql + """
                LIMIT 1
            """, [referencia, empresa_id, proveedor_id, proveedor_id] + producto_scope_params),
            ("""
                SELECT id
                FROM productos p
                WHERE p.empresa_id = ? AND (p.codigo = ? OR p.codigo_interno = ?)
                  """ + producto_scope_sql + """
                LIMIT 1
            """, [empresa_id, referencia, referencia] + producto_scope_params)
        ]:
            try:
                row = conn.execute(sql, params).fetchone()
                if row:
                    return row['id']
            except Exception:
                pass

    palabras = [p.lower() for p in re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+', descripcion) if len(p) >= 3]
    if not palabras:
        return None

    productos = conn.execute("""
        SELECT id, nombre
        FROM productos p
        WHERE p.empresa_id = ? AND COALESCE(p.activo, 1) = 1
          """ + producto_scope_sql + """
    """, [empresa_id] + producto_scope_params).fetchall()

    mejor_match = None
    mejor_score = 0
    for p in productos:
        nombre = (p['nombre'] or '').lower()
        score = sum(1 for palabra in palabras if palabra in nombre)
        if score > mejor_score:
            mejor_score = score
            mejor_match = p['id']

    return mejor_match if mejor_score >= 2 else None


def _guardar_alias_producto_compra(cursor, empresa_id, proveedor_id, producto_id, descripcion, referencia=None):
    descripcion = (descripcion or '').strip()
    referencia = (referencia or '').strip() or None
    descripcion_normalizada = _normalizar_texto_match(descripcion)

    if not producto_id or (not referencia and not descripcion_normalizada):
        return

    existente = cursor.execute("""
        SELECT id
        FROM productos_aliases_compra
        WHERE empresa_id = ?
          AND producto_id = ?
          AND COALESCE(proveedor_id, 0) = COALESCE(?, 0)
          AND COALESCE(referencia, '') = COALESCE(?, '')
          AND COALESCE(descripcion_normalizada, '') = COALESCE(?, '')
        LIMIT 1
    """, (empresa_id, producto_id, proveedor_id, referencia, descripcion_normalizada)).fetchone()

    if existente:
        cursor.execute("""
            UPDATE productos_aliases_compra
            SET veces = COALESCE(veces, 0) + 1,
                activo = 1,
                fecha_ultima = CURRENT_TIMESTAMP,
                descripcion = COALESCE(NULLIF(?, ''), descripcion)
            WHERE id = ?
        """, (descripcion, existente['id']))
        return

    cursor.execute("""
        INSERT INTO productos_aliases_compra (
            empresa_id,
            proveedor_id,
            producto_id,
            referencia,
            descripcion,
            descripcion_normalizada,
            activo,
            veces
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, 1)
    """, (
        empresa_id,
        proveedor_id,
        producto_id,
        referencia,
        descripcion or None,
        descripcion_normalizada or None,
    ))


def _registrar_auditoria_compra(
    cursor,
    empresa_id,
    tipo,
    referencia_tipo=None,
    referencia_id=None,
    detalle=None,
    departamento_id=None,
    proveedor_id=None,
    estado_anterior=None,
    estado_nuevo=None,
    usuario=None,
):
    cursor.execute("""
        INSERT INTO compras_auditoria (
            empresa_id, tipo, referencia_tipo, referencia_id, usuario,
            departamento_id, proveedor_id, estado_anterior, estado_nuevo, detalle
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        empresa_id,
        tipo,
        referencia_tipo,
        referencia_id,
        usuario or session.get('user_nombre') or 'sistema',
        departamento_id,
        proveedor_id,
        estado_anterior,
        estado_nuevo,
        detalle,
    ))


def _registrar_historico_precio_compra(
    cursor,
    empresa_id,
    producto_id,
    proveedor_id,
    precio_nuevo,
    precio_anterior=None,
    origen='validacion_economica',
    referencia_tipo=None,
    referencia_id=None,
    usuario=None,
):
    precio_nuevo = _to_float_safe(precio_nuevo, None)
    precio_anterior = _to_float_safe(precio_anterior, None)
    if not producto_id or precio_nuevo is None or precio_nuevo <= 0:
        return
    if precio_anterior is not None and abs(precio_anterior - precio_nuevo) < 0.000001:
        return
    cursor.execute("""
        INSERT INTO compras_historico_precios (
            empresa_id, producto_id, proveedor_id, precio_anterior, precio_nuevo,
            origen, referencia_tipo, referencia_id, usuario
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        empresa_id,
        producto_id,
        proveedor_id,
        precio_anterior,
        precio_nuevo,
        origen,
        referencia_tipo,
        referencia_id,
        usuario or session.get('user_nombre') or 'sistema',
    ))


def _actualizar_precio_producto_proveedor_compra(
    cursor,
    empresa_id,
    producto_id,
    proveedor_id,
    precio_nuevo,
    origen='validacion_economica',
    referencia_tipo=None,
    referencia_id=None,
    usuario=None,
    centro_id=None,
):
    """Actualiza precio proveedor-producto con validacion fuerte y trazabilidad."""
    producto_id = _to_int_or_none(producto_id)
    proveedor_id = _to_int_or_none(proveedor_id)
    precio_nuevo = _to_float_safe(precio_nuevo, None)
    if not producto_id or not proveedor_id or precio_nuevo is None or precio_nuevo <= 0:
        return False

    conn = cursor.connection
    centro_id = centro_id or _centro_activo_id()
    producto_scope_sql, producto_scope_params = _centro_sql_columna(conn, "productos", "p", centro_id, permite_global=True)
    proveedor_scope_sql, proveedor_scope_params = _centro_sql_columna(conn, "proveedores", "pr", centro_id, permite_global=True)

    producto = cursor.execute("""
        SELECT id
        FROM productos p
        WHERE p.id = ?
          AND p.empresa_id = ?
          AND COALESCE(activo, 1) = 1
          """ + producto_scope_sql + """
    """, [producto_id, empresa_id] + producto_scope_params).fetchone()
    proveedor = cursor.execute("""
        SELECT id
        FROM proveedores pr
        WHERE pr.id = ?
          AND pr.empresa_id = ?
          AND COALESCE(pr.activo, 1) = 1
          """ + proveedor_scope_sql + """
    """, [proveedor_id, empresa_id] + proveedor_scope_params).fetchone()
    if not producto or not proveedor:
        return False

    existente = cursor.execute("""
        SELECT id, precio, precio_compra, precio_ultimo, precio_vigente
        FROM productos_proveedores
        WHERE producto_id = ?
          AND proveedor_id = ?
        LIMIT 1
    """, (producto_id, proveedor_id)).fetchone()
    precio_anterior = None
    if existente:
        for col in ('precio_vigente', 'precio_ultimo', 'precio_compra', 'precio'):
            if col in existente.keys() and existente[col] is not None:
                precio_anterior = _to_float_safe(existente[col], None)
                break
        cursor.execute("""
            UPDATE productos_proveedores
            SET precio = ?,
                precio_compra = ?,
                precio_ultimo = ?,
                precio_vigente = ?,
                activo = 1,
                fecha_actualizacion = CURRENT_DATE
            WHERE id = ?
        """, (precio_nuevo, precio_nuevo, precio_nuevo, precio_nuevo, existente['id']))
    else:
        cursor.execute("""
            INSERT INTO productos_proveedores (
                producto_id, proveedor_id, precio, precio_compra,
                precio_ultimo, precio_vigente, activo, preferente
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, 0)
        """, (producto_id, proveedor_id, precio_nuevo, precio_nuevo, precio_nuevo, precio_nuevo))

    _registrar_historico_precio_compra(
        cursor,
        empresa_id,
        producto_id,
        proveedor_id,
        precio_nuevo,
        precio_anterior=precio_anterior,
        origen=origen,
        referencia_tipo=referencia_tipo,
        referencia_id=referencia_id,
        usuario=usuario,
    )
    return True


def _diagnostico_recepcion_economica(cursor, recepcion_id, empresa_id):
    pendientes_ocr = cursor.execute("""
        SELECT COUNT(*) AS total
        FROM documento_compra_lineas_detectadas dl
        JOIN documentos_compra doc ON doc.id = dl.documento_id
        JOIN recepciones r ON r.id = ?
        WHERE (doc.recepcion_id = r.id OR doc.id = r.documento_adjunto_id)
          AND dl.producto_id IS NULL
    """, (recepcion_id,)).fetchone()

    sin_precio = cursor.execute("""
        SELECT COUNT(*) AS total
        FROM lineas_recepcion
        WHERE recepcion_id = ?
          AND (empresa_id = ? OR empresa_id IS NULL)
          AND COALESCE(aceptado, 1) = 1
          AND COALESCE(cantidad_recibida, 0) > 0
          AND (precio_unitario IS NULL OR COALESCE(precio_unitario, 0) <= 0)
    """, (recepcion_id, empresa_id)).fetchone()

    diferencias_precio = cursor.execute("""
        SELECT COUNT(*) AS total
        FROM lineas_recepcion lr
        JOIN recepciones r ON r.id = lr.recepcion_id
        JOIN lineas_pedido lp ON lp.pedido_id = r.pedido_id AND lp.producto_id = lr.producto_id
        WHERE lr.recepcion_id = ?
          AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
          AND lr.precio_unitario IS NOT NULL
          AND lp.precio_unitario IS NOT NULL
          AND ABS(COALESCE(lr.precio_unitario, 0) - COALESCE(lp.precio_unitario, 0)) >
              CASE
                  WHEN ABS(COALESCE(lp.precio_unitario, 0)) * 0.05 > 0.01
                  THEN ABS(COALESCE(lp.precio_unitario, 0)) * 0.05
                  ELSE 0.01
              END
    """, (recepcion_id, empresa_id)).fetchone()

    incidencias = cursor.execute("""
        SELECT COUNT(*) AS total
        FROM lineas_recepcion lr
        LEFT JOIN incidencias i ON i.id = lr.incidencia_id
        WHERE lr.recepcion_id = ?
          AND lr.incidencia_id IS NOT NULL
          AND COALESCE(i.estado, 'abierta') NOT IN ('resuelta', 'cerrada', 'validada')
    """, (recepcion_id,)).fetchone()

    datos = {
        'pendientes_ocr': int(pendientes_ocr['total'] or 0) if pendientes_ocr else 0,
        'sin_precio': int(sin_precio['total'] or 0) if sin_precio else 0,
        'diferencias_precio': int(diferencias_precio['total'] or 0) if diferencias_precio else 0,
        'incidencias_abiertas': int(incidencias['total'] or 0) if incidencias else 0,
    }
    datos['puede_validar_rapido'] = not any(datos.values())
    return datos



def obtener_o_crear_proveedor(cursor, texto, empresa_id):
    import re

    lineas = texto.splitlines()

    for linea in lineas[:5]:  # primeras líneas = proveedor
        limpio = linea.strip()

        if len(limpio) < 5:
            continue

        if any(p in limpio.lower() for p in ['cif', 'factura', 'ticket']):
            continue

        nombre = re.sub(r'[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 ]', '', limpio).strip()

        if len(nombre) < 4:
            continue

        existente = cursor.execute("""
            SELECT id FROM proveedores
            WHERE nombre LIKE ?
            AND empresa_id = ?
            LIMIT 1
        """, (f"%{nombre}%", empresa_id)).fetchone()

        if existente:
            return existente['id']

        cursor.execute("""
            INSERT INTO proveedores (nombre, empresa_id)
            VALUES (?, ?)
        """, (nombre, empresa_id))

        return cursor.lastrowid

    return None

def obtener_o_crear_producto(cursor, descripcion, empresa_id, referencia=None):
    producto_id = buscar_producto_por_texto(cursor.connection, descripcion, empresa_id, referencia)
    if producto_id:
        return producto_id
    nombre = (descripcion or '').strip()
    if len(nombre) < 3:
        return None
    try:
        cursor.execute("""
            INSERT INTO productos (
                nombre, codigo, activo, empresa_id,
                visible_en_compras, visible_en_produccion,
                visible_en_venta, permite_compra_directa
            )
            VALUES (?, ?, 1, ?, 1, 0, 0, 1)
        """, (nombre, referencia or None, empresa_id))
    except Exception:
        cursor.execute("""
            INSERT INTO productos (nombre, codigo, activo, empresa_id)
            VALUES (?, ?, 1, ?)
        """, (nombre, referencia or None, empresa_id))
    return cursor.lastrowid

# ============================================
# RUTAS DE COMPRAS
# ============================================

@compras_bp.route('/pedidos')
@login_required
def lista_pedidos():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()
    
    proveedor_id = request.args.get('proveedor', '')
    departamento_id = request.args.get('departamento', '')
    estado = request.args.get('estado', '')
    fecha_desde = request.args.get('fecha_desde', '')
    fecha_hasta = request.args.get('fecha_hasta', '')
    tipo_fecha = request.args.get('tipo_fecha', 'pedido')
    tipo_pedido = request.args.get('tipo_pedido', '')
    busqueda = request.args.get('buscar', '').strip()
    simulacion = request.args.get('simulacion', '').strip()
    
    scope_sql, scope_params, centro_activo_id = _scope_pedidos_centro(conn, 'p', empresa_id)
    mostrar_laboratorio_hotel1 = (
        simulacion == 'hotel1'
        or (int(empresa_id or 0) == 1 and int(centro_activo_id or 0) == 3)
    )
    query = f"""
        SELECT p.*, pr.nombre as proveedor_nombre, d.nombre AS departamento_nombre,
               (SELECT COUNT(*) FROM lineas_pedido WHERE pedido_id = p.id) as num_lineas,
               (SELECT SUM(cantidad_solicitada * precio_unitario) FROM lineas_pedido WHERE pedido_id = p.id) as total_pedido
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        LEFT JOIN departamentos d ON d.id = p.departamento_id
        WHERE {scope_sql}
    """
    params = list(scope_params)
    
    if proveedor_id:
        query += " AND p.proveedor_id = ?"
        params.append(proveedor_id)
    if departamento_id:
        query += " AND p.departamento_id = ?"
        params.append(departamento_id)
    if estado:
        query += " AND p.estado = ?"
        params.append(estado)
    if tipo_pedido == 'mantenimiento':
        query += " AND COALESCE(p.tipo_pedido, '') = ?"
        params.append('mantenimiento')
    elif tipo_pedido == 'general':
        query += " AND COALESCE(p.tipo_pedido, '') <> ?"
        params.append('mantenimiento')
    if simulacion == 'hotel1':
        query += " AND p.numero_pedido LIKE ?"
        params.append('COMPRA-H1-%')
    if busqueda:
        like_busqueda = f"%{busqueda}%"
        query += """ AND (
            p.numero_pedido LIKE ?
            OR COALESCE(pr.nombre, '') LIKE ?
            OR COALESCE(d.nombre, '') LIKE ?
        )"""
        params.extend([like_busqueda, like_busqueda, like_busqueda])
    if fecha_desde:
        if tipo_fecha == 'pedido':
            query += " AND p.fecha_pedido >= ?"
        elif tipo_fecha == 'entrega':
            query += " AND p.fecha_entrega >= ?"
        params.append(fecha_desde)
    if fecha_hasta:
        if tipo_fecha == 'pedido':
            query += " AND p.fecha_pedido <= ?"
        elif tipo_fecha == 'entrega':
            query += " AND p.fecha_entrega <= ?"
        params.append(fecha_hasta)
    
    query += " ORDER BY p.fecha_pedido DESC"
    
    pedidos = conn.execute(query, params).fetchall()

    resumen_rows = conn.execute(f"""
        SELECT COALESCE(p.estado, 'borrador') AS estado,
               COUNT(*) AS total,
               COALESCE(SUM((
                   SELECT COALESCE(SUM(COALESCE(lp.cantidad_solicitada, 0) * COALESCE(lp.precio_unitario, 0)), 0)
                   FROM lineas_pedido lp
                   WHERE lp.pedido_id = p.id
               )), 0) AS importe
        FROM pedidos p
        WHERE {scope_sql}
        GROUP BY COALESCE(p.estado, 'borrador')
    """, scope_params).fetchall()
    resumen_pedidos = {r['estado']: {'total': r['total'], 'importe': r['importe']} for r in resumen_rows}
    total_resumen_pedidos = {
        'total': sum(int(r['total'] or 0) for r in resumen_rows),
        'importe': sum(float(r['importe'] or 0) for r in resumen_rows),
    }

    resumen_simulacion_compras = conn.execute(f"""
        SELECT COUNT(*) AS total,
               MIN(date(p.fecha_pedido)) AS fecha_desde,
               MAX(date(p.fecha_pedido)) AS fecha_hasta,
               COALESCE(SUM((
                   SELECT COALESCE(SUM(COALESCE(lp.cantidad_solicitada, 0) * COALESCE(lp.precio_unitario, 0)), 0)
                   FROM lineas_pedido lp
                   WHERE lp.pedido_id = p.id
               )), 0) AS importe,
               (SELECT COUNT(*)
                FROM recepciones r
                JOIN pedidos p2 ON p2.id = r.pedido_id
                WHERE p2.numero_pedido LIKE 'COMPRA-H1-%'
                  AND p2.empresa_id = p.empresa_id) AS recepciones,
               SUM(CASE WHEN COALESCE(p.estado, '') IN ('recibido', 'cerrado') THEN 1 ELSE 0 END) AS completados
        FROM pedidos p
        WHERE {scope_sql}
          AND p.numero_pedido LIKE 'COMPRA-H1-%'
    """, scope_params).fetchone()

    proveedores = proveedores_select(conn, empresa_id)
    departamentos = _departamentos_visibles_compras(conn, empresa_id)
    
    conn.close()
    
    response = make_response(render_template(
        'compras/pedidos.html',
        pedidos=pedidos,
        proveedores=proveedores,
        departamentos=departamentos,
        resumen_pedidos=resumen_pedidos,
        total_resumen_pedidos=total_resumen_pedidos,
        resumen_simulacion_compras=resumen_simulacion_compras,
        mostrar_laboratorio_hotel1=mostrar_laboratorio_hotel1,
        filtros={
            'proveedor': proveedor_id,
            'departamento': departamento_id,
            'estado': estado,
            'fecha_desde': fecha_desde,
            'fecha_hasta': fecha_hasta,
            'tipo_fecha': tipo_fecha,
            'tipo_pedido': tipo_pedido,
            'buscar': busqueda,
            'simulacion': simulacion,
        },
        centro_activo_id=centro_activo_id,
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def _calcular_necesidades_compra(
    conn,
    empresa_id,
    proveedor_id='',
    familia='',
    solo_sin_proveedor=False,
    incluir_planificacion=False,
    fecha_desde=None,
    fecha_hasta=None,
    centro_id=None,
):
    centro_id = centro_id or _centro_activo_id()
    columnas_productos = _columnas_tabla(conn.cursor(), "productos")
    columnas_almacenes = _columnas_tabla(conn.cursor(), "almacenes")
    columnas_proveedores = _columnas_tabla(conn.cursor(), "proveedores")
    stock_centro_sql = ""
    stock_centro_params = []
    almacen_pref_centro_sql = ""
    almacen_pref_centro_params = []
    producto_centro_sql = ""
    producto_centro_params = []
    proveedor_pp_centro_sql = ""
    proveedor_pp_centro_params = []
    proveedor_prod_centro_sql = ""
    proveedor_prod_centro_params = []
    pendiente_centro_sql = ""
    pendiente_centro_params = []

    if centro_id and "centro_id" in columnas_almacenes:
        stock_centro_sql = " AND a.centro_id = ?"
        stock_centro_params.append(centro_id)
        almacen_pref_centro_sql = " AND (ap.centro_id IS NULL OR ap.centro_id = ?)"
        almacen_pref_centro_params.append(centro_id)

    if centro_id and "centro_id" in columnas_productos:
        producto_centro_sql = " AND (p.centro_id IS NULL OR p.centro_id = ?)"
        producto_centro_params.append(centro_id)

    if centro_id and "centro_id" in columnas_proveedores:
        proveedor_pp_centro_sql = " AND (pr_pp.centro_id IS NULL OR pr_pp.centro_id = ?)"
        proveedor_pp_centro_params.append(centro_id)
        proveedor_prod_centro_sql = " AND (pr_prod.centro_id IS NULL OR pr_prod.centro_id = ?)"
        proveedor_prod_centro_params.append(centro_id)

    if centro_id:
        dept_ids, punto_ids = _ids_centro(conn, empresa_id, centro_id)
        pedido_cols = _columnas_tabla(conn.cursor(), "pedidos")
        condiciones_pendientes = []
        if dept_ids and "departamento_id" in pedido_cols:
            condiciones_pendientes.append(f"ped.departamento_id IN ({','.join('?' for _ in dept_ids)})")
            pendiente_centro_params.extend(dept_ids)
        if dept_ids and "departamento_solicitante_id" in pedido_cols:
            condiciones_pendientes.append(f"ped.departamento_solicitante_id IN ({','.join('?' for _ in dept_ids)})")
            pendiente_centro_params.extend(dept_ids)
        if punto_ids and "punto_venta_id" in pedido_cols:
            condiciones_pendientes.append(f"ped.punto_venta_id IN ({','.join('?' for _ in punto_ids)})")
            pendiente_centro_params.extend(punto_ids)
        if condiciones_pendientes:
            pendiente_centro_sql = " AND (" + " OR ".join(condiciones_pendientes) + ")"

    filas = conn.execute("""
        SELECT p.id, p.nombre, p.codigo, p.unidad, p.familia, p.precio,
               p.departamento_responsable_id,
               p.almacen_preferente_id,
               ap.departamento_id AS almacen_departamento_id,
               COALESCE(NULLIF(st.stock_minimo_almacen, 0), p.stock_minimo, 0) AS stock_minimo,
               COALESCE(st.stock_total, p.cantidad, 0) AS stock_total,
               COALESCE(pend.cantidad_pendiente, 0) AS cantidad_pendiente,
               pp.proveedor_id AS proveedor_asociado_id,
               pr_pp.nombre AS proveedor_asociado_nombre,
               p.proveedor_id AS proveedor_producto_id,
               pr_prod.nombre AS proveedor_producto_nombre,
               COALESCE(pp.precio_vigente, pp.precio_ultimo, pp.precio_compra, pp.precio, p.precio, 0) AS precio_referencia
        FROM productos p
        LEFT JOIN (
            SELECT sa.producto_id,
                   COALESCE(SUM(sa.cantidad), 0) AS stock_total,
                   COALESCE(MAX(sa.stock_minimo), 0) AS stock_minimo_almacen
            FROM stock_almacen sa
            JOIN almacenes a ON a.id = sa.almacen_id
            WHERE a.empresa_id = ?
              AND COALESCE(a.activo, 1) = 1
              """ + stock_centro_sql + """
            GROUP BY sa.producto_id
        ) st ON st.producto_id = p.id
        LEFT JOIN (
            SELECT lp.producto_id,
                   SUM(
                       CASE
                           WHEN COALESCE(lp.cantidad_solicitada, 0) > COALESCE(lp.cantidad_recibida, 0)
                           THEN COALESCE(lp.cantidad_solicitada, 0) - COALESCE(lp.cantidad_recibida, 0)
                           ELSE 0
                       END
                   ) AS cantidad_pendiente
            FROM lineas_pedido lp
            JOIN pedidos ped ON ped.id = lp.pedido_id
            WHERE ped.empresa_id = ?
              AND COALESCE(ped.estado, 'borrador') IN (
                  'borrador', 'pendiente_autorizacion', 'autorizado', 'enviado', 'recibido_parcial'
              )
              """ + pendiente_centro_sql + """
            GROUP BY lp.producto_id
        ) pend ON pend.producto_id = p.id
        LEFT JOIN productos_proveedores pp ON pp.id = (
            SELECT pp2.id
            FROM productos_proveedores pp2
            WHERE pp2.producto_id = p.id
              AND COALESCE(pp2.activo, 1) = 1
            ORDER BY COALESCE(pp2.es_principal, 0) DESC,
                     COALESCE(pp2.preferente, 0) DESC,
                     pp2.id DESC
            LIMIT 1
        )
        LEFT JOIN proveedores pr_pp
               ON pr_pp.id = pp.proveedor_id
              AND COALESCE(pr_pp.activo, 1) = 1
              AND pr_pp.empresa_id = ?
              """ + proveedor_pp_centro_sql + """
        LEFT JOIN proveedores pr_prod
               ON pr_prod.id = p.proveedor_id
              AND COALESCE(pr_prod.activo, 1) = 1
              AND pr_prod.empresa_id = ?
              """ + proveedor_prod_centro_sql + """
        LEFT JOIN almacenes ap
               ON ap.id = p.almacen_preferente_id
              AND COALESCE(ap.activo, 1) = 1
              AND (ap.empresa_id = ? OR ap.empresa_id IS NULL)
              """ + almacen_pref_centro_sql + """
        WHERE p.empresa_id = ?
          AND COALESCE(p.activo, 1) = 1
          AND COALESCE(p.visible_en_compras, 1) = 1
          AND COALESCE(p.permite_compra_directa, 1) = 1
          """ + producto_centro_sql + """
        ORDER BY p.familia, p.nombre
    """, (
        [empresa_id]
        + stock_centro_params
        + [empresa_id]
        + pendiente_centro_params
        + [empresa_id]
        + proveedor_pp_centro_params
        + [empresa_id]
        + proveedor_prod_centro_params
        + [empresa_id]
        + almacen_pref_centro_params
        + [empresa_id]
        + producto_centro_params
    )).fetchall()

    necesidades = []
    mapa_departamentos = _mapa_departamentos_compra(conn, empresa_id)
    nombres_departamentos = {
        int(row['id']): row['nombre']
        for row in conn.execute("""
            SELECT id, nombre
            FROM departamentos
            WHERE COALESCE(activo, 1) = 1
              AND (empresa_id = ? OR empresa_id IS NULL)
        """, (empresa_id,)).fetchall()
    }
    filas_por_producto = {fila['id']: fila for fila in filas}

    def _necesidad_desde_fila(
        fila,
        cantidad_sugerida,
        origen='stock_minimo',
        demanda_planificada=0.0,
        departamento_override_id=None,
    ):
        minimo = _to_float_safe(fila['stock_minimo'], 0.0)
        stock = _to_float_safe(fila['stock_total'], 0.0)
        pendiente = _to_float_safe(fila['cantidad_pendiente'], 0.0)
        disponible_futuro = stock + pendiente

        proveedor_asociado_id = fila['proveedor_asociado_id'] if fila['proveedor_asociado_nombre'] else None
        proveedor_producto_id = fila['proveedor_producto_id'] if fila['proveedor_producto_nombre'] else None
        proveedor_final_id = proveedor_asociado_id or proveedor_producto_id
        proveedor_final_nombre = fila['proveedor_asociado_nombre'] or fila['proveedor_producto_nombre']

        if proveedor_id and str(proveedor_final_id or '') != str(proveedor_id):
            return None
        if familia and (fila['familia'] or '') != familia:
            return None
        if solo_sin_proveedor and proveedor_final_id:
            return None

        departamento_id = _to_int_or_none(departamento_override_id)
        if not departamento_id:
            departamento_id = _resolver_departamento_producto_compra(
                mapa_departamentos,
                fila['departamento_responsable_id'],
                fila['almacen_departamento_id'],
                fila['familia'],
                fila['nombre'],
            )
        return {
            'id': fila['id'],
            'nombre': fila['nombre'],
            'codigo': fila['codigo'],
            'unidad': fila['unidad'] or '',
            'familia': fila['familia'] or 'otros',
            'stock_minimo': minimo,
            'stock_total': stock,
            'cantidad_pendiente': pendiente,
            'disponible_futuro': disponible_futuro,
            'cantidad_sugerida': round(cantidad_sugerida, 3),
            'proveedor_id': proveedor_final_id,
            'proveedor_nombre': proveedor_final_nombre,
            'precio_referencia': _to_float_safe(fila['precio_referencia'], 0.0),
            'critico': stock <= 0,
            'departamento_id': departamento_id,
            'departamento_nombre': nombres_departamentos.get(departamento_id, 'Sin departamento') if departamento_id else 'Sin departamento',
            'origen_necesidad': origen,
            'demanda_planificada': round(demanda_planificada, 3),
        }

    necesidades_por_producto = {}
    for fila in filas:
        minimo = _to_float_safe(fila['stock_minimo'], 0.0)
        stock = _to_float_safe(fila['stock_total'], 0.0)
        pendiente = _to_float_safe(fila['cantidad_pendiente'], 0.0)
        disponible_futuro = stock + pendiente
        if minimo <= 0 or disponible_futuro > minimo:
            continue

        objetivo_reposicion = max(minimo * 2, minimo + 1)
        sugerida = max(objetivo_reposicion - disponible_futuro, 0.0)
        necesidad = _necesidad_desde_fila(fila, sugerida, origen='stock_minimo')
        if necesidad:
            necesidades_por_producto[fila['id']] = necesidad

    if incluir_planificacion and fecha_desde and fecha_hasta:
        try:
            from blueprints.produccion.routes import _calcular_necesidades_planificacion
            datos_plan = _calcular_necesidades_planificacion(conn, empresa_id, fecha_desde, fecha_hasta)
        except Exception:
            datos_plan = {"productos": []}

        for producto_plan in datos_plan.get("productos", []):
            producto_id = producto_plan["producto_id"]
            faltante = _to_float_safe(producto_plan.get("faltante"), 0.0)
            if faltante <= 0:
                continue
            fila = filas_por_producto.get(producto_id)
            if not fila:
                continue
            existente = necesidades_por_producto.get(producto_id)
            departamento_plan_id = _to_int_or_none(producto_plan.get("departamento_id"))
            if existente:
                existente["cantidad_sugerida"] = round(max(_to_float_safe(existente["cantidad_sugerida"], 0.0), faltante), 3)
                existente["origen_necesidad"] = "planificacion"
                existente["demanda_planificada"] = round(_to_float_safe(producto_plan.get("cantidad_necesaria"), 0.0), 3)
                if departamento_plan_id:
                    existente["departamento_id"] = departamento_plan_id
                    existente["departamento_nombre"] = nombres_departamentos.get(departamento_plan_id, 'Sin departamento')
                continue
            necesidad = _necesidad_desde_fila(
                fila,
                faltante,
                origen='planificacion',
                demanda_planificada=_to_float_safe(producto_plan.get("cantidad_necesaria"), 0.0),
                departamento_override_id=departamento_plan_id,
            )
            if necesidad:
                necesidades_por_producto[producto_id] = necesidad

        if _table_exists(conn, "simulador_alertas_mise_en_place"):
            params = [empresa_id, fecha_desde, fecha_hasta]
            filtro_centro = ""
            centro_filtrado = _to_int_or_none(centro_id)
            if centro_filtrado:
                filtro_centro = " AND centro_id = ?"
                params.append(centro_filtrado)
            alertas_mise = conn.execute(f"""
                SELECT producto_id,
                       MIN(fecha_alerta) AS primer_aviso,
                       MIN(fecha_servicio) AS primer_servicio,
                       MAX(fecha_servicio) AS ultimo_servicio,
                       MAX(prioridad) AS prioridad,
                       GROUP_CONCAT(DISTINCT tipo) AS tipos,
                       SUM(COALESCE(cantidad, 0)) AS cantidad_necesaria,
                       COUNT(*) AS avisos
                FROM simulador_alertas_mise_en_place
                WHERE empresa_id = ?
                  AND estado = 'pendiente'
                  AND producto_id IS NOT NULL
                  AND date(fecha_servicio) BETWEEN date(?) AND date(?)
                  {filtro_centro}
                GROUP BY producto_id
            """, params).fetchall()

            for alerta in alertas_mise:
                producto_id = alerta["producto_id"]
                fila = filas_por_producto.get(producto_id)
                if not fila:
                    continue
                demanda = _to_float_safe(alerta["cantidad_necesaria"], 0.0)
                if demanda <= 0:
                    continue
                stock = _to_float_safe(fila["stock_total"], 0.0)
                pendiente = _to_float_safe(fila["cantidad_pendiente"], 0.0)
                faltante = max(demanda - stock - pendiente, 0.0)
                if faltante <= 0:
                    continue

                existente = necesidades_por_producto.get(producto_id)
                if existente:
                    existente["cantidad_sugerida"] = round(max(_to_float_safe(existente["cantidad_sugerida"], 0.0), faltante), 3)
                    origen_actual = existente.get("origen_necesidad") or "stock_minimo"
                    existente["origen_necesidad"] = "mise_en_place" if origen_actual == "stock_minimo" else f"{origen_actual}+mise"
                    existente["demanda_planificada"] = round(max(_to_float_safe(existente.get("demanda_planificada"), 0.0), demanda), 3)
                    existente["mise_alertas"] = int(alerta["avisos"] or 0)
                    existente["primer_aviso"] = alerta["primer_aviso"]
                    existente["primer_servicio"] = alerta["primer_servicio"]
                    existente["tipos_mise"] = alerta["tipos"] or ""
                    continue

                necesidad = _necesidad_desde_fila(
                    fila,
                    faltante,
                    origen='mise_en_place',
                    demanda_planificada=demanda,
                )
                if necesidad:
                    necesidad["mise_alertas"] = int(alerta["avisos"] or 0)
                    necesidad["primer_aviso"] = alerta["primer_aviso"]
                    necesidad["primer_servicio"] = alerta["primer_servicio"]
                    necesidad["tipos_mise"] = alerta["tipos"] or ""
                    necesidades_por_producto[producto_id] = necesidad

    necesidades = sorted(
        necesidades_por_producto.values(),
        key=lambda n: (
            0 if 'mise' in (n.get('origen_necesidad') or '') else 1 if n.get('origen_necesidad') == 'planificacion' else 2,
            n['familia'],
            n['nombre'],
        )
    )
    return necesidades


@compras_bp.route('/necesidades', methods=['GET', 'POST'])
@login_required
def necesidades_compra():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = _centro_activo_id()
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    bloqueo = _requiere_gestion_compras(conn, destino='compras.lista_pedidos')
    if bloqueo:
        return bloqueo

    if request.method == 'POST':
        departamento_fallback_id = _to_int_or_none(request.form.get('departamento_id'))
        fecha_entrega = request.form.get('fecha_entrega') or None
        observaciones = (request.form.get('observaciones') or '').strip()
        seleccionados = request.form.getlist('seleccionado')
        incluir_planificacion_post = str(request.values.get('incluir_planificacion') or '').lower() in {'1', 'true', 'on', 'si', 'sí'}
        fecha_desde_post = request.values.get('fecha_desde') or datetime.now().strftime('%Y-%m-%d')
        fecha_hasta_post = request.values.get('fecha_hasta') or (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')

        if not seleccionados:
            conn.close()
            flash('Marca al menos un producto con proveedor.', 'warning')
            return redirect(url_for('compras.necesidades_compra'))

        mapa_departamentos = _mapa_departamentos_compra(conn, empresa_id)
        departamentos_planificacion = {}
        if incluir_planificacion_post and fecha_desde_post and fecha_hasta_post:
            try:
                from blueprints.produccion.routes import _calcular_necesidades_planificacion
                datos_plan = _calcular_necesidades_planificacion(conn, empresa_id, fecha_desde_post, fecha_hasta_post)
                departamentos_planificacion = {
                    int(p["producto_id"]): _to_int_or_none(p.get("departamento_id"))
                    for p in datos_plan.get("productos", [])
                    if p.get("producto_id") and _to_int_or_none(p.get("departamento_id"))
                }
            except Exception:
                departamentos_planificacion = {}
        pedidos_por_proveedor_departamento = {}
        omitidos = 0
        for producto_id_raw in seleccionados:
            producto_id = _to_int_or_none(producto_id_raw)
            if not producto_id:
                continue

            cantidad = _to_float_safe(request.form.get(f'cantidad_{producto_id}'), 0.0)
            proveedor_id = _to_int_or_none(request.form.get(f'proveedor_{producto_id}'))
            departamento_producto_id = _to_int_or_none(request.form.get(f'departamento_producto_{producto_id}'))
            if cantidad <= 0 or not proveedor_id:
                omitidos += 1
                continue

            producto_centro_sql = ""
            producto_centro_params = []
            if centro_id and "centro_id" in _columnas_tabla(conn.cursor(), "productos"):
                producto_centro_sql = " AND (centro_id IS NULL OR centro_id = ?)"
                producto_centro_params.append(centro_id)

            producto = conn.execute("""
                SELECT id
                FROM productos
                WHERE id = ?
                  AND empresa_id = ?
                  AND COALESCE(activo, 1) = 1
                  AND COALESCE(visible_en_compras, 1) = 1
                  AND COALESCE(permite_compra_directa, 1) = 1
                  """ + producto_centro_sql + """
            """, [producto_id, empresa_id] + producto_centro_params).fetchone()
            producto_ctx = conn.execute("""
                SELECT p.departamento_responsable_id,
                       p.almacen_preferente_id,
                       p.familia,
                       p.nombre,
                       a.departamento_id AS almacen_departamento_id
                FROM productos p
                LEFT JOIN almacenes a ON a.id = p.almacen_preferente_id
                WHERE p.id = ?
                  AND p.empresa_id = ?
                  """ + producto_centro_sql.replace("centro_id", "p.centro_id") + """
            """, [producto_id, empresa_id] + producto_centro_params).fetchone()
            proveedor_centro_sql = ""
            proveedor_centro_params = []
            if centro_id and "centro_id" in _columnas_tabla(conn.cursor(), "proveedores"):
                proveedor_centro_sql = " AND (centro_id IS NULL OR centro_id = ?)"
                proveedor_centro_params.append(centro_id)
            proveedor = conn.execute("""
                SELECT id
                FROM proveedores
                WHERE id = ?
                  AND empresa_id = ?
                  AND COALESCE(activo, 1) = 1
                  """ + proveedor_centro_sql + """
            """, [proveedor_id, empresa_id] + proveedor_centro_params).fetchone()

            proveedor_compra = _producto_proveedor_compra_valido(conn, empresa_id, producto_id, proveedor_id, centro_id)

            if not producto or not proveedor or not proveedor_compra:
                omitidos += 1
                continue

            precio = proveedor_compra['precio']

            departamento_planificacion_id = departamentos_planificacion.get(producto_id)
            if departamento_planificacion_id:
                departamento_producto_id = departamento_planificacion_id

            if not departamento_producto_id and producto_ctx:
                departamento_producto_id = _resolver_departamento_producto_compra(
                    mapa_departamentos,
                    producto_ctx['departamento_responsable_id'],
                    producto_ctx['almacen_departamento_id'],
                    producto_ctx['familia'],
                    producto_ctx['nombre'],
                    departamento_fallback_id,
                )

            if not departamento_producto_id:
                omitidos += 1
                continue

            if not _departamento_form_permitido(departamento_producto_id):
                omitidos += 1
                continue

            clave = (proveedor_id, departamento_producto_id)
            pedidos_por_proveedor_departamento.setdefault(clave, []).append({
                'producto_id': producto_id,
                'cantidad': cantidad,
                'precio': precio,
                'origen_precio': proveedor_compra['origen'],
            })

        if not pedidos_por_proveedor_departamento:
            conn.close()
            flash('No se ha creado ningun pedido. Revisa proveedores, cantidades y departamento responsable.', 'warning')
            return redirect(url_for('compras.necesidades_compra'))

        cursor = conn.cursor()
        fecha = datetime.now().strftime("%Y%m%d")
        cursor.execute(
            "SELECT COUNT(*) as total FROM pedidos WHERE fecha_pedido = date('now') AND empresa_id = ?",
            (empresa_id,)
        )
        total_hoy = int(cursor.fetchone()['total'] or 0)
        pedidos_creados = []
        usuario = _usuario_actual_compras()
        detalle_origen = 'Generado desde necesidades de compra'
        if observaciones:
            detalle_origen = f"{detalle_origen}. {observaciones}"

        for (proveedor_id, departamento_id), lineas in pedidos_por_proveedor_departamento.items():
            total_hoy += 1
            numero_pedido = f"PED-{fecha}-{total_hoy:03d}"
            cursor.execute("""
                INSERT INTO pedidos (
                    numero_pedido, proveedor_id, departamento_id, departamento_solicitante_id, fecha_entrega,
                    observaciones, creado_por, estado, empresa_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'borrador', ?)
            """, (
                numero_pedido,
                proveedor_id,
                departamento_id,
                departamento_id,
                fecha_entrega,
                detalle_origen,
                usuario,
                empresa_id
            ))
            pedido_id = cursor.lastrowid
            pedidos_creados.append(pedido_id)

            for linea in lineas:
                cursor.execute("""
                    INSERT INTO lineas_pedido (
                        pedido_id, producto_id, cantidad_solicitada, precio_unitario
                    )
                    VALUES (?, ?, ?, ?)
                """, (pedido_id, linea['producto_id'], linea['cantidad'], linea['precio']))

            _registrar_auditoria_compra(
                cursor,
                empresa_id,
                'pedido_creado_necesidad',
                'pedido',
                pedido_id,
                f'{len(lineas)} linea(s) desde necesidades de compra',
                departamento_id=departamento_id,
                proveedor_id=proveedor_id,
                estado_nuevo='borrador',
                usuario=usuario,
            )

        conn.commit()
        conn.close()

        if omitidos:
            flash(f'{omitidos} producto(s) no se han incluido por falta de proveedor o cantidad.', 'warning')
        if len(pedidos_creados) == 1:
            flash('Pedido en borrador creado desde necesidades de compra.', 'success')
            return redirect(url_for('compras.ver_pedido', pedido_id=pedidos_creados[0]))

        flash(f'Se han creado {len(pedidos_creados)} pedidos en borrador, agrupados por proveedor y departamento.', 'success')
        return redirect(url_for('compras.lista_pedidos'))

    proveedor_id = request.args.get('proveedor', '')
    familia = request.args.get('familia', '')
    solo_sin_proveedor = request.args.get('sin_proveedor') == '1'
    incluir_planificacion = request.args.get('incluir_planificacion') == '1'
    fecha_desde = request.args.get('fecha_desde') or datetime.now().strftime('%Y-%m-%d')
    fecha_hasta = request.args.get('fecha_hasta') or (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')

    necesidades = _calcular_necesidades_compra(
        conn,
        empresa_id,
        proveedor_id=proveedor_id,
        familia=familia,
        solo_sin_proveedor=solo_sin_proveedor,
        incluir_planificacion=incluir_planificacion,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        centro_id=session.get('centro_activo_id'),
    )
    proveedores = proveedores_select(conn, empresa_id)
    departamentos = _departamentos_visibles_compras(conn, empresa_id)
    familias = conn.execute("""
        SELECT DISTINCT familia
        FROM productos
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND COALESCE(visible_en_compras, 1) = 1
          AND familia IS NOT NULL
          AND TRIM(familia) != ''
        ORDER BY familia
    """, (empresa_id,)).fetchall()

    resumen = {
        'total': len(necesidades),
        'criticos': sum(1 for n in necesidades if n['critico']),
        'sin_proveedor': sum(1 for n in necesidades if not n['proveedor_id']),
        'planificacion': sum(1 for n in necesidades if n.get('origen_necesidad') == 'planificacion'),
        'mise_en_place': sum(1 for n in necesidades if 'mise' in (n.get('origen_necesidad') or '')),
        'importe_estimado': sum(
            (n['cantidad_sugerida'] or 0) * (n['precio_referencia'] or 0)
            for n in necesidades
            if n['proveedor_id']
        ),
    }
    conn.close()

    return render_template(
        'compras/necesidades.html',
        necesidades=necesidades,
        proveedores=proveedores,
        departamentos=departamentos,
        familias=familias,
        resumen=resumen,
        filtros={
            'proveedor': proveedor_id,
            'familia': familia,
            'sin_proveedor': solo_sin_proveedor,
            'incluir_planificacion': incluir_planificacion,
            'fecha_desde': fecha_desde,
            'fecha_hasta': fecha_hasta,
        },
        now=datetime.now(),
    )

@compras_bp.route('/pedidos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_pedido():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    bloqueo = _requiere_gestion_compras(conn, destino='compras.lista_pedidos')
    if bloqueo:
        return bloqueo

    if request.method == "POST":
        proveedor_id = _to_int_or_none(request.form.get("proveedor_id"))
        departamento_id = _to_int_or_none(request.form.get("departamento_id"))
        fecha_entrega = request.form.get("fecha_entrega", "")
        observaciones = request.form.get("observaciones", "")

        if not proveedor_id:
            conn.close()
            flash('Debes seleccionar proveedor.', 'warning')
            return redirect(url_for('compras.nuevo_pedido'))

        if not departamento_id:
            conn.close()
            flash('Debes seleccionar departamento.', 'warning')
            return redirect(url_for('compras.nuevo_pedido'))

        if not _departamento_form_permitido(departamento_id):
            conn.close()
            flash('No puedes crear pedidos para otro departamento.', 'warning')
            return redirect(url_for('compras.nuevo_pedido'))

        proveedor = conn.execute("""
            SELECT id
            FROM proveedores
            WHERE id = ?
              AND empresa_id = ?
              AND COALESCE(activo, 1) = 1
        """, (proveedor_id, empresa_id)).fetchone()
        if not proveedor:
            conn.close()
            flash('Proveedor no encontrado o inactivo.', 'danger')
            return redirect(url_for('compras.nuevo_pedido'))

        numero_pedido = generar_numero_pedido()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pedidos (
                numero_pedido, proveedor_id, departamento_id, departamento_solicitante_id, fecha_entrega,
                observaciones, creado_por, estado, empresa_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'borrador', ?)
        """, (
            numero_pedido,
            proveedor_id,
            departamento_id,
            departamento_id,
            fecha_entrega,
            observaciones,
            session.get('user_nombre', 'admin'),
            empresa_id
        ))

        pedido_id = cursor.lastrowid
        _registrar_auditoria_compra(
            cursor,
            empresa_id,
            'pedido_creado',
            referencia_tipo='pedido',
            referencia_id=pedido_id,
            detalle=f'Pedido {numero_pedido} creado en borrador',
            departamento_id=departamento_id,
            proveedor_id=proveedor_id,
            estado_nuevo='borrador',
        )
        conn.commit()
        conn.close()

        flash(f' Pedido {numero_pedido} creado', 'success')
        return redirect(url_for('compras.editar_pedido', pedido_id=pedido_id))

    proveedores = proveedores_select(conn, empresa_id, columnas="*")
    departamentos = _departamentos_visibles_compras(conn, empresa_id)
    conn.close()
    return render_template('compras/nuevo_pedido.html', proveedores=proveedores, departamentos=departamentos)

@compras_bp.route('/pedidos/<int:pedido_id>')
@login_required
def ver_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()
    
    scope_sql, scope_params = _scope_departamento('p', empresa_id)
    pedido = conn.execute(f"""
        SELECT p.*, pr.nombre as proveedor_nombre,
               COALESCE(NULLIF(pr.email_pedidos, ''), pr.email) AS email,
               COALESCE(NULLIF(pr.telefono_pedidos, ''), pr.telefono) AS telefono,
               d.nombre AS departamento_nombre
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        LEFT JOIN departamentos d ON d.id = p.departamento_id
        WHERE p.id = ?
          AND {scope_sql}
    """, [pedido_id, *scope_params]).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))
    
    lineas = conn.execute("""
        SELECT l.*, prod.nombre, prod.unidad,
               (
                   SELECT lr.precio_unitario
                   FROM lineas_recepcion lr
                   JOIN recepciones r ON r.id = lr.recepcion_id
                   WHERE r.pedido_id = l.pedido_id
                     AND lr.producto_id = l.producto_id
                     AND lr.precio_unitario IS NOT NULL
                     AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
                   ORDER BY r.id DESC, lr.id DESC
                   LIMIT 1
               ) AS ultimo_precio_recepcion,
               (
                   SELECT COALESCE(SUM(COALESCE(lr.importe_total, COALESCE(lr.cantidad_recibida, 0) * COALESCE(lr.precio_unitario, 0))), 0)
                   FROM lineas_recepcion lr
                   JOIN recepciones r ON r.id = lr.recepcion_id
                   WHERE r.pedido_id = l.pedido_id
                     AND lr.producto_id = l.producto_id
                     AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
               ) AS importe_recepcion_real
        FROM lineas_pedido l
        JOIN productos prod ON l.producto_id = prod.id
        WHERE l.pedido_id = ? AND prod.empresa_id = ?
    """, (empresa_id, empresa_id, pedido_id, empresa_id)).fetchall()
    
    tiene_recepciones = any(float((l['cantidad_recibida'] or 0)) > 0 for l in lineas)
    resumen_lineas = _resumen_lineas_pedido(cur, pedido_id)
    estado = pedido['estado'] or 'borrador'
    puede_gestionar = usuario_puede_gestionar_compras_departamento(pedido['departamento_id'])
    puede_solicitar = (
        (usuario_tiene_permiso('compras_solicitar') or puede_gestionar)
        and estado in ('borrador', 'rechazado')
        and not tiene_recepciones
    )
    acciones = {
        'puede_editar': puede_gestionar and estado in ('borrador', 'rechazado') and not tiene_recepciones,
        'puede_solicitar': puede_solicitar,
        'puede_autorizar': puede_gestionar and estado == 'pendiente_autorizacion',
        'puede_rechazar': puede_gestionar and estado == 'pendiente_autorizacion',
        'puede_enviar_email': puede_gestionar and estado in ('autorizado', 'enviado'),
        'puede_eliminar': puede_gestionar and estado == 'borrador' and not tiene_recepciones,
        'puede_cancelar': puede_gestionar and (estado in ('borrador', 'rechazado') or (estado == 'enviado' and not tiene_recepciones)),
        'puede_enviar': puede_gestionar and estado == 'autorizado',
        'puede_recibir': estado in ('enviado', 'recibido_parcial'),
        'puede_cerrar': puede_gestionar and estado == 'recibido',
        'puede_imprimir': True,
    }

    recepciones_pedido = conn.execute("""
        SELECT r.*, dc.id AS documento_id, dc.nombre_archivo,
               (SELECT COUNT(*) FROM lineas_recepcion lr WHERE lr.recepcion_id = r.id) AS num_lineas,
               (SELECT COUNT(*) FROM lineas_recepcion lr WHERE lr.recepcion_id = r.id AND lr.incidencia_id IS NOT NULL) AS num_incidencias,
               (SELECT COALESCE(SUM(COALESCE(lr.importe_total, COALESCE(lr.cantidad_recibida, 0) * COALESCE(lr.precio_unitario, 0))), 0)
                FROM lineas_recepcion lr
                WHERE lr.recepcion_id = r.id) AS importe_recepcion
        FROM recepciones r
        LEFT JOIN documentos_compra dc ON dc.id = r.documento_adjunto_id
        WHERE r.pedido_id = ?
          AND r.empresa_id = ?
        ORDER BY r.id DESC
    """, (pedido_id, empresa_id)).fetchall()

    documentos_pedido = conn.execute("""
        SELECT dc.*, r.numero_albaran, r.fecha_recepcion
        FROM documentos_compra dc
        LEFT JOIN recepciones r ON r.id = dc.recepcion_id
        WHERE dc.pedido_id = ?
           OR dc.recepcion_id IN (
                SELECT id FROM recepciones WHERE pedido_id = ? AND empresa_id = ?
           )
        ORDER BY dc.id DESC
    """, (pedido_id, pedido_id, empresa_id)).fetchall()

    validaciones_pedido = conn.execute("""
        SELECT rv.*, r.numero_albaran, r.fecha_recepcion
        FROM recepcion_validaciones rv
        JOIN recepciones r ON r.id = rv.recepcion_id
        WHERE r.pedido_id = ?
          AND r.empresa_id = ?
        ORDER BY rv.id DESC
        LIMIT 50
    """, (pedido_id, empresa_id)).fetchall()

    auditoria_pedido = conn.execute("""
        SELECT a.*
        FROM compras_auditoria a
        WHERE a.empresa_id = ?
          AND (
                (a.referencia_tipo = 'pedido' AND a.referencia_id = ?)
                OR (
                    a.referencia_tipo = 'recepcion'
                    AND a.referencia_id IN (
                        SELECT id FROM recepciones WHERE pedido_id = ? AND empresa_id = ?
                    )
                )
          )
        ORDER BY a.id DESC
        LIMIT 80
    """, (empresa_id, pedido_id, pedido_id, empresa_id)).fetchall()

    mantenimiento_origen = None
    try:
        if conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'mantenimiento_pedidos_compra'").fetchone():
            mantenimiento_origen = conn.execute("""
                SELECT mpc.*, ot.id AS orden_id, ot.numero_orden,
                       r.codigo AS repuesto_codigo, r.nombre AS repuesto_nombre
                FROM mantenimiento_pedidos_compra mpc
                LEFT JOIN ordenes_trabajo ot ON ot.id = mpc.orden_id
                LEFT JOIN repuestos r ON r.id = mpc.repuesto_id
                WHERE mpc.pedido_id = ?
                  AND COALESCE(mpc.empresa_id, ?) = ?
                ORDER BY mpc.id DESC
                LIMIT 1
            """, (pedido_id, empresa_id, empresa_id)).fetchone()
    except Exception:
        mantenimiento_origen = None

    conn.close()
    return render_template(
        'compras/ver_pedido.html',
        pedido=pedido,
        lineas=lineas,
        acciones=acciones,
        resumen_lineas=resumen_lineas,
        recepciones_pedido=recepciones_pedido,
        documentos_pedido=documentos_pedido,
        validaciones_pedido=validaciones_pedido,
        auditoria_pedido=auditoria_pedido,
        mantenimiento_origen=mantenimiento_origen,
        ver_precios=_usuario_puede_ver_precios_compras(),
    )

@compras_bp.route('/pedidos/<int:pedido_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    try:
        conn.execute('PRAGMA busy_timeout = 5000')
    except Exception:
        pass

    scope_sql, scope_params = _scope_departamento('p', empresa_id)
    pedido = conn.execute(f"""
        SELECT p.*, d.nombre AS departamento_nombre
        FROM pedidos p
        LEFT JOIN departamentos d ON d.id = p.departamento_id
        WHERE p.id = ?
          AND {scope_sql}
    """, [pedido_id, *scope_params]).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    bloqueo = _requiere_gestion_compras(conn, pedido['departamento_id'], 'compras.lista_pedidos')
    if bloqueo:
        return bloqueo

    estado_actual = pedido['estado'] or 'borrador'
    if estado_actual not in ('borrador', 'rechazado'):
        conn.close()
        flash('Solo se puede editar un pedido en borrador o rechazado.', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    def _ultimo_precio_producto_proveedor(producto_id, proveedor_id):
        """Devuelve el último precio conocido para ese producto/proveedor o None.

        No inventa precios. Si no hay precio guardado, deja la línea sin precio
        para que Compras/Dirección lo valide económicamente después.
        """
        if not producto_id:
            return None

        columnas_pp = _columnas_tabla(cur, 'productos_proveedores')
        columnas_p = _columnas_tabla(cur, 'productos')

        precio_cols_pp = [
            c for c in ['precio_vigente', 'precio_ultimo', 'precio_medio', 'precio_compra', 'precio']
            if c in columnas_pp
        ]

        if precio_cols_pp:
            select_precio = 'COALESCE(' + ', '.join(precio_cols_pp) + ') AS precio_actual'
            sql = f"""
                SELECT {select_precio}
                FROM productos_proveedores
                WHERE producto_id = ?
            """
            params = [producto_id]

            if 'proveedor_id' in columnas_pp and proveedor_id:
                sql += " AND (proveedor_id = ? OR proveedor_id IS NULL)"
                params.append(proveedor_id)

            if 'activo' in columnas_pp:
                sql += " AND COALESCE(activo, 1) = 1"

            orden = []
            if 'proveedor_id' in columnas_pp and proveedor_id:
                orden.append('CASE WHEN proveedor_id = ? THEN 0 ELSE 1 END')
                params.append(proveedor_id)
            if 'es_principal' in columnas_pp:
                orden.append('COALESCE(es_principal, 0) DESC')
            if 'preferente' in columnas_pp:
                orden.append('COALESCE(preferente, 0) DESC')
            orden.append('id DESC')
            sql += ' ORDER BY ' + ', '.join(orden) + ' LIMIT 1'

            row = conn.execute(sql, tuple(params)).fetchone()
            if row and row['precio_actual'] is not None:
                return _to_float_safe(row['precio_actual'], None)

        if 'precio' in columnas_p:
            row = conn.execute("""
                SELECT precio
                FROM productos
                WHERE id = ? AND empresa_id = ?
                LIMIT 1
            """, (producto_id, empresa_id)).fetchone()
            if row and row['precio'] is not None:
                return _to_float_safe(row['precio'], None)

        return None

    if request.method == "POST":
        productos = request.form.getlist("producto_id[]")
        cantidades = request.form.getlist("cantidad[]")
        precios = request.form.getlist("precio[]")
        departamento_id = request.form.get("departamento_id") or pedido['departamento_id']
        fecha_entrega = request.form.get("fecha_entrega", "")
        observaciones = request.form.get("observaciones", "")

        try:
            if departamento_id and not _departamento_form_permitido(departamento_id):
                conn.close()
                flash('No puedes mover este pedido a otro departamento.', 'warning')
                return redirect(url_for('compras.editar_pedido', pedido_id=pedido_id))

            nuevo_estado = 'borrador' if estado_actual == 'rechazado' else estado_actual
            conn.execute("""
                UPDATE pedidos
                SET departamento_id = ?,
                    fecha_entrega = ?,
                    observaciones = ?,
                    estado = ?,
                    motivo_rechazo = NULL,
                    rechazado_por = NULL,
                    fecha_rechazo = NULL
                WHERE id = ? AND empresa_id = ?
            """, (departamento_id, fecha_entrega, observaciones, nuevo_estado, pedido_id, empresa_id))

            conn.execute("DELETE FROM lineas_pedido WHERE pedido_id = ?", (pedido_id,))

            lineas_guardadas = 0
            proveedor_id = pedido['proveedor_id']

            for i in range(len(productos)):
                producto_id = _to_int_or_none(productos[i] if i < len(productos) else '')
                cantidad_raw = cantidades[i] if i < len(cantidades) else ''

                cantidad = _to_float_safe(cantidad_raw, 0.0)
                if not producto_id or cantidad <= 0:
                    continue

                proveedor_compra = _producto_proveedor_compra_valido(conn, empresa_id, producto_id, proveedor_id)
                if not proveedor_compra:
                    continue
                precio_unitario = proveedor_compra['precio']

                conn.execute("""
                    INSERT INTO lineas_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario)
                    VALUES (?, ?, ?, ?)
                """, (pedido_id, producto_id, cantidad, precio_unitario))
                lineas_guardadas += 1

            if lineas_guardadas <= 0:
                conn.rollback()
                flash('No se ha guardado el pedido porque no hay lineas validas para este proveedor.', 'warning')
                return redirect(url_for('compras.editar_pedido', pedido_id=pedido_id))

            _registrar_auditoria_compra(
                cur,
                empresa_id,
                'pedido_editado',
                referencia_tipo='pedido',
                referencia_id=pedido_id,
                detalle=f'Lineas guardadas: {lineas_guardadas}',
                departamento_id=departamento_id,
                proveedor_id=pedido['proveedor_id'],
                estado_anterior=estado_actual,
                estado_nuevo=nuevo_estado,
            )
            conn.commit()
            flash(f'Pedido actualizado correctamente. Líneas guardadas: {lineas_guardadas}.', 'success')
            return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

        except Exception as e:
            conn.rollback()
            flash(f'Error actualizando pedido: {str(e)}', 'danger')
            return redirect(url_for('compras.editar_pedido', pedido_id=pedido_id))

        finally:
            conn.close()

    productos = conn.execute("""
        SELECT p.id,
               p.nombre,
               p.codigo,
               COALESCE(pp.precio_vigente, pp.precio_ultimo, pp.precio_compra, pp.precio, p.precio, 0) AS precio_actual
        FROM productos p
        LEFT JOIN productos_proveedores pp
               ON pp.id = (
                    SELECT pp2.id
                    FROM productos_proveedores pp2
                    WHERE pp2.producto_id = p.id
                      AND pp2.proveedor_id = ?
                      AND COALESCE(pp2.activo, 1) = 1
                    ORDER BY COALESCE(pp2.es_principal, 0) DESC,
                             COALESCE(pp2.preferente, 0) DESC,
                             pp2.id DESC
                    LIMIT 1
               )
        WHERE p.empresa_id = ?
          AND COALESCE(p.activo, 1) = 1
          AND COALESCE(p.visible_en_compras, 1) = 1
          AND COALESCE(p.permite_compra_directa, 1) = 1
          AND (pp.id IS NOT NULL OR p.proveedor_id = ?)
        ORDER BY p.nombre
    """, (pedido['proveedor_id'], empresa_id, pedido['proveedor_id'])).fetchall()
    departamentos = _departamentos_visibles_compras(conn, empresa_id)

    lineas = conn.execute("""
        SELECT l.*, p.nombre, p.unidad
        FROM lineas_pedido l
        JOIN productos p ON l.producto_id = p.id
        WHERE l.pedido_id = ? AND p.empresa_id = ?
    """, (pedido_id, empresa_id)).fetchall()

    conn.close()
    return render_template('compras/editar_pedido.html', pedido=pedido, productos=productos, lineas=lineas, departamentos=departamentos)

@compras_bp.route('/pedidos/<int:pedido_id>/cambiar-estado/<string:estado>', methods=['POST'])
@login_required
def cambiar_estado_pedido(pedido_id, estado):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    pedido_permiso = conn.execute(
        "SELECT departamento_id, proveedor_id, estado FROM pedidos WHERE id = ? AND empresa_id = ?",
        (pedido_id, empresa_id),
    ).fetchone()
    if not pedido_permiso:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))
    bloqueo = _requiere_gestion_compras(conn, pedido_permiso['departamento_id'])
    if bloqueo:
        return bloqueo
    scope_sql, scope_params = _scope_departamento('pedidos', empresa_id)

    estado_actual = pedido_permiso['estado'] or 'borrador'
    transiciones = {
        'borrador': {'pendiente_autorizacion'},
        'rechazado': {'borrador', 'pendiente_autorizacion'},
        'pendiente_autorizacion': {'autorizado', 'rechazado'},
        'autorizado': {'enviado'},
    }
    if estado not in transiciones.get(estado_actual, set()):
        conn.close()
        flash('Cambio de estado no permitido. Usa el circuito normal del pedido.', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    ok_autorizacion, mensaje = (True, '')
    if estado in ('pendiente_autorizacion', 'autorizado'):
        ok_autorizacion, mensaje = _pedido_preparado_para_autorizacion(cur, pedido_id)
    if not ok_autorizacion:
        conn.close()
        flash(mensaje, 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    usuario = _usuario_actual_compras()
    campos_extra = ""
    params_extra = []
    if estado == 'pendiente_autorizacion':
        campos_extra = ", solicitado_por = ?, fecha_solicitud_autorizacion = datetime('now'), motivo_rechazo = NULL, rechazado_por = NULL, fecha_rechazo = NULL"
        params_extra = [usuario]
    elif estado == 'autorizado':
        campos_extra = ", aprobado_por = ?, fecha_aprobacion = datetime('now'), motivo_rechazo = NULL, rechazado_por = NULL, fecha_rechazo = NULL"
        params_extra = [usuario]
    elif estado == 'rechazado':
        campos_extra = ", motivo_rechazo = COALESCE(motivo_rechazo, 'Pedido devuelto para revision.'), rechazado_por = ?, fecha_rechazo = datetime('now')"
        params_extra = [usuario]
    elif estado == 'enviado':
        campos_extra = ", fecha_envio = datetime('now')"

    cursor = cur.execute(
        f"UPDATE pedidos SET estado = ?{campos_extra} WHERE id = ? AND COALESCE(estado, 'borrador') = ? AND {scope_sql}",
        [estado, *params_extra, pedido_id, estado_actual, *scope_params],
    )
    if cursor.rowcount:
        _registrar_auditoria_compra(
            cur,
            empresa_id,
            'pedido_estado',
            referencia_tipo='pedido',
            referencia_id=pedido_id,
            detalle=f'Estado cambiado a {estado}',
            departamento_id=pedido_permiso['departamento_id'],
            proveedor_id=pedido_permiso['proveedor_id'],
            estado_anterior=pedido_permiso['estado'],
            estado_nuevo=estado,
            usuario=usuario,
        )
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))
    flash(f' Estado cambiado a {estado}', 'success')
    return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))


@compras_bp.route('/pedidos/<int:pedido_id>/solicitar-autorizacion', methods=['POST'])
@login_required
def solicitar_autorizacion_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    scope_sql, scope_params = _scope_departamento('p', empresa_id)
    pedido = conn.execute(f"SELECT p.* FROM pedidos p WHERE p.id = ? AND {scope_sql}", [pedido_id, *scope_params]).fetchone()
    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))
    puede_solicitar_departamento = (
        usuario_puede_gestionar_compras_departamento(pedido['departamento_id'])
        or (
            usuario_tiene_permiso('compras_solicitar')
            and usuario_puede_ver_departamento(pedido['departamento_id'], ROLES_COMPRAS_GLOBAL)
        )
    )
    if not puede_solicitar_departamento:
        conn.close()
        flash('No tienes permiso para solicitar este pedido.', 'warning')
        return redirect(url_for('compras.lista_pedidos'))
    if pedido['estado'] not in ('borrador', 'rechazado'):
        conn.close()
        flash('Solo se puede solicitar autorizacion desde borrador o rechazado.', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    ok_autorizacion, mensaje = _pedido_preparado_para_autorizacion(cur, pedido_id)
    if not ok_autorizacion:
        conn.close()
        flash(mensaje, 'warning')
        return redirect(url_for('compras.editar_pedido', pedido_id=pedido_id))

    usuario = _usuario_actual_compras()
    cur.execute(f"""
        UPDATE pedidos
        SET estado = 'pendiente_autorizacion',
            solicitado_por = ?,
            fecha_solicitud_autorizacion = datetime('now'),
            motivo_rechazo = NULL,
            rechazado_por = NULL,
            fecha_rechazo = NULL
        WHERE id = ? AND empresa_id = ? AND estado = ?
          AND id IN (SELECT p.id FROM pedidos p WHERE {scope_sql})
    """, [usuario, pedido_id, empresa_id, pedido['estado'], *scope_params])
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        flash('El pedido ha cambiado. Recarga y revisa su estado.', 'warning')
        return redirect(url_for('compras.lista_pedidos'))
    _registrar_auditoria_compra(
        cur,
        empresa_id,
        'pedido_solicitado',
        referencia_tipo='pedido',
        referencia_id=pedido_id,
        detalle='Pedido enviado a autorizacion',
        departamento_id=pedido['departamento_id'],
        proveedor_id=pedido['proveedor_id'],
        estado_anterior=pedido['estado'],
        estado_nuevo='pendiente_autorizacion',
        usuario=usuario,
    )
    conn.commit()
    conn.close()
    flash('Pedido enviado a autorizacion.', 'success')
    return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))


@compras_bp.route('/pedidos/<int:pedido_id>/autorizar', methods=['POST'])
@login_required
def autorizar_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    scope_sql, scope_params = _scope_departamento('p', empresa_id)
    pedido = conn.execute(f"SELECT p.* FROM pedidos p WHERE p.id = ? AND {scope_sql}", [pedido_id, *scope_params]).fetchone()
    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))
    bloqueo = _requiere_gestion_compras(conn, pedido['departamento_id'], 'compras.lista_pedidos')
    if bloqueo:
        return bloqueo
    if pedido['estado'] != 'pendiente_autorizacion':
        conn.close()
        flash('Solo se puede autorizar un pedido pendiente.', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    ok_autorizacion, mensaje = _pedido_preparado_para_autorizacion(cur, pedido_id)
    if not ok_autorizacion:
        conn.close()
        flash(mensaje, 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    usuario = _usuario_actual_compras()
    cur.execute(f"""
        UPDATE pedidos
        SET estado = 'autorizado',
            aprobado_por = ?,
            fecha_aprobacion = datetime('now'),
            motivo_rechazo = NULL,
            rechazado_por = NULL,
            fecha_rechazo = NULL
        WHERE id = ? AND empresa_id = ? AND estado = 'pendiente_autorizacion'
          AND id IN (SELECT p.id FROM pedidos p WHERE {scope_sql})
    """, [usuario, pedido_id, empresa_id, *scope_params])
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        flash('El pedido ha cambiado. Recarga y revisa su estado.', 'warning')
        return redirect(url_for('compras.lista_pedidos'))
    _registrar_auditoria_compra(
        cur,
        empresa_id,
        'pedido_autorizado',
        referencia_tipo='pedido',
        referencia_id=pedido_id,
        detalle='Pedido autorizado para envio a proveedor',
        departamento_id=pedido['departamento_id'],
        proveedor_id=pedido['proveedor_id'],
        estado_anterior=pedido['estado'],
        estado_nuevo='autorizado',
        usuario=usuario,
    )
    conn.commit()
    conn.close()
    flash('Pedido autorizado.', 'success')
    return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))


@compras_bp.route('/pedidos/<int:pedido_id>/rechazar', methods=['POST'])
@login_required
def rechazar_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    scope_sql, scope_params = _scope_departamento('p', empresa_id)
    pedido = conn.execute(f"SELECT p.* FROM pedidos p WHERE p.id = ? AND {scope_sql}", [pedido_id, *scope_params]).fetchone()
    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    bloqueo = _requiere_gestion_compras(conn, pedido['departamento_id'], 'compras.lista_pedidos')
    if bloqueo:
        return bloqueo

    if pedido['estado'] != 'pendiente_autorizacion':
        conn.close()
        flash('Solo se puede rechazar un pedido pendiente de autorizacion.', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    motivo = (request.form.get('motivo_rechazo') or '').strip()
    if not motivo:
        motivo = 'Pedido devuelto para revision.'
    usuario = _usuario_actual_compras()

    cur.execute(f"""
        UPDATE pedidos
        SET estado = 'rechazado',
            motivo_rechazo = ?,
            rechazado_por = ?,
            fecha_rechazo = datetime('now')
        WHERE id = ? AND empresa_id = ? AND estado = 'pendiente_autorizacion'
          AND id IN (SELECT p.id FROM pedidos p WHERE {scope_sql})
    """, [motivo, usuario, pedido_id, empresa_id, *scope_params])
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        flash('El pedido ha cambiado. Recarga y revisa su estado.', 'warning')
        return redirect(url_for('compras.lista_pedidos'))
    _registrar_auditoria_compra(
        cur,
        empresa_id,
        'pedido_rechazado',
        referencia_tipo='pedido',
        referencia_id=pedido_id,
        detalle=f'Pedido rechazado: {motivo}',
        departamento_id=pedido['departamento_id'],
        proveedor_id=pedido['proveedor_id'],
        estado_anterior=pedido['estado'],
        estado_nuevo='rechazado',
        usuario=usuario,
    )
    conn.commit()
    conn.close()
    flash('Pedido rechazado y devuelto para corregir.', 'warning')
    return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))



@compras_bp.route('/pedidos/<int:pedido_id>/cancelar', methods=['POST'])
@login_required
def cancelar_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cursor = conn.cursor()

    scope_sql, scope_params = _scope_departamento('pedidos', empresa_id)
    pedido = cursor.execute(
        f"SELECT * FROM pedidos WHERE id = ? AND {scope_sql}",
        [pedido_id, *scope_params],
    ).fetchone()

    if not pedido:
        conn.close()
        flash(' Pedido no encontrado', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    bloqueo = _requiere_gestion_compras(conn, pedido['departamento_id'])
    if bloqueo:
        return bloqueo

    tiene_recepciones = _pedido_tiene_recepciones(cursor, pedido_id)

    if pedido['estado'] in ('borrador', 'rechazado'):
        pass
    elif pedido['estado'] == 'enviado' and not tiene_recepciones:
        pass
    else:
        conn.close()
        flash(' Solo se puede cancelar un borrador, rechazado o un pedido enviado sin recepciones', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    cursor.execute(f"""
        UPDATE pedidos
        SET estado = 'cancelado',
            cancelado = 1,
            motivo_cancelacion = COALESCE(motivo_cancelacion, 'Cancelado manualmente')
        WHERE id = ?
          AND {scope_sql}
    """, [pedido_id, *scope_params])
    _registrar_auditoria_compra(
        cursor,
        empresa_id,
        'pedido_cancelado',
        referencia_tipo='pedido',
        referencia_id=pedido_id,
        detalle='Pedido cancelado',
        departamento_id=pedido['departamento_id'],
        proveedor_id=pedido['proveedor_id'],
        estado_anterior=pedido['estado'],
        estado_nuevo='cancelado',
    )
    conn.commit()
    conn.close()

    flash(' Pedido cancelado correctamente', 'success')
    return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))


@compras_bp.route('/pedidos/<int:pedido_id>/cerrar', methods=['POST'])
@login_required
def cerrar_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    pedido_permiso = conn.execute(
        "SELECT departamento_id FROM pedidos WHERE id = ? AND empresa_id = ?",
        (pedido_id, empresa_id),
    ).fetchone()
    if not pedido_permiso:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))
    bloqueo = _requiere_gestion_compras(conn, pedido_permiso['departamento_id'])
    if bloqueo:
        return bloqueo
    scope_sql, scope_params = _scope_departamento('pedidos', empresa_id)

    cur = conn.cursor()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ? AND empresa_id = ?", (pedido_id, empresa_id)).fetchone()
    pendientes_economicos = conn.execute("""
        SELECT COUNT(*) AS total
        FROM recepciones
        WHERE pedido_id = ?
          AND empresa_id = ?
          AND COALESCE(validada_economicamente, 0) = 0
    """, (pedido_id, empresa_id)).fetchone()
    if pendientes_economicos and int(pendientes_economicos['total'] or 0) > 0:
        conn.close()
        flash('No se puede cerrar: quedan recepciones pendientes de validacion economica.', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))
    cursor = cur.execute(f"""
        UPDATE pedidos
        SET estado = 'cerrado',
            cerrado = 1,
            fecha_cierre = datetime('now')
        WHERE id = ?
          AND {scope_sql}
    """, [pedido_id, *scope_params])
    if cursor.rowcount and pedido:
        _registrar_auditoria_compra(
            cur,
            empresa_id,
            'pedido_cerrado',
            referencia_tipo='pedido',
            referencia_id=pedido_id,
            detalle='Pedido cerrado',
            departamento_id=pedido['departamento_id'],
            proveedor_id=pedido['proveedor_id'],
            estado_anterior=pedido['estado'],
            estado_nuevo='cerrado',
        )

    conn.commit()
    conn.close()

    if cursor.rowcount == 0:
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    flash('Pedido cerrado correctamente', 'success')
    return redirect(url_for('compras.lista_pedidos'))


@compras_bp.route('/pedidos/<int:pedido_id>/eliminar', methods=['POST'])
@login_required
def eliminar_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    scope_sql, scope_params = _scope_departamento('p', empresa_id)

    pedido = conn.execute(
        f"SELECT estado, departamento_id FROM pedidos p WHERE p.id = ? AND {scope_sql}",
        [pedido_id, *scope_params],
    ).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    bloqueo = _requiere_gestion_compras(conn, pedido['departamento_id'])
    if bloqueo:
        return bloqueo

    if pedido['estado'] != 'borrador':
        conn.close()
        flash('Solo se pueden eliminar pedidos en borrador', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    conn.execute("DELETE FROM lineas_pedido WHERE pedido_id = ?", (pedido_id,))
    conn.execute("DELETE FROM pedidos WHERE id = ? AND empresa_id = ?", (pedido_id, empresa_id))
    conn.commit()
    conn.close()

    flash('Pedido eliminado', 'success')
    return redirect(url_for('compras.lista_pedidos'))

@compras_bp.route('/pedidos/<int:pedido_id>/imprimir')
@login_required
def imprimir_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    scope_sql, scope_params = _scope_departamento('p', empresa_id)
    pedido_row = conn.execute(f"""
        SELECT p.*,
               pr.nombre AS proveedor_nombre,
               COALESCE(NULLIF(pr.email_pedidos, ''), pr.email) AS email,
               COALESCE(NULLIF(pr.telefono_pedidos, ''), pr.telefono) AS telefono,
               pr.nif,
               d.nombre AS departamento_nombre
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        LEFT JOIN departamentos d ON d.id = p.departamento_id
        WHERE p.id = ?
          AND {scope_sql}
    """, [pedido_id, *scope_params]).fetchone()

    if not pedido_row:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    pedido = dict(pedido_row)
    pedido['empresa_nombre'] = _obtener_nombre_empresa(conn, empresa_id)
    pedido['establecimiento_nombre'] = pedido['empresa_nombre']

    lineas = conn.execute("""
        SELECT l.*,
               prod.nombre,
               prod.unidad,
               prod.codigo,
               prod.referencia_proveedor
        FROM lineas_pedido l
        JOIN productos prod ON l.producto_id = prod.id
        WHERE l.pedido_id = ? AND prod.empresa_id = ?
        ORDER BY prod.nombre
    """, (pedido_id, empresa_id)).fetchall()

    conn.close()
    return render_template('compras/imprimir_pedido.html', pedido=pedido, lineas=lineas)

@compras_bp.route('/pedidos/<int:pedido_id>/enviar-email', methods=['POST'])
@login_required
def enviar_pedido_por_email(pedido_id):
    from flask_mail import Message

    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    scope_sql, scope_params = _scope_departamento('p', empresa_id)
    pedido_row = conn.execute(f"""
        SELECT p.*,
               pr.nombre AS proveedor_nombre,
               COALESCE(NULLIF(pr.email_pedidos, ''), pr.email) AS email,
               COALESCE(NULLIF(pr.telefono_pedidos, ''), pr.telefono) AS telefono,
               pr.nif,
               d.nombre AS departamento_nombre
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        LEFT JOIN departamentos d ON d.id = p.departamento_id
        WHERE p.id = ?
          AND {scope_sql}
    """, [pedido_id, *scope_params]).fetchone()

    if not pedido_row:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    bloqueo = _requiere_gestion_compras(conn, pedido_row['departamento_id'])
    if bloqueo:
        return bloqueo

    if pedido_row['estado'] not in ('autorizado', 'enviado'):
        conn.close()
        flash('El pedido debe estar autorizado antes de enviarse por email.', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    ok_autorizacion, mensaje = _pedido_preparado_para_autorizacion(conn.cursor(), pedido_id)
    if not ok_autorizacion:
        conn.close()
        flash(mensaje, 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    pedido = dict(pedido_row)
    pedido['empresa_nombre'] = _obtener_nombre_empresa(conn, empresa_id)
    pedido['establecimiento_nombre'] = pedido['empresa_nombre']

    email_proveedor = (pedido.get('email') or '').strip()
    if not email_proveedor:
        conn.close()
        flash('El proveedor no tiene email configurado.', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    lineas = conn.execute("""
        SELECT l.*,
               prod.nombre,
               prod.unidad,
               prod.codigo,
               prod.referencia_proveedor
        FROM lineas_pedido l
        JOIN productos prod ON l.producto_id = prod.id
        WHERE l.pedido_id = ? AND prod.empresa_id = ?
        ORDER BY prod.nombre
    """, (pedido_id, empresa_id)).fetchall()
    conn.close()

    numero_pedido = pedido.get('numero_pedido') or f'PED-{pedido_id}'
    establecimiento = pedido.get('empresa_nombre') or 'Mi establecimiento'
    departamento = pedido.get('departamento_nombre') or '-'
    responsable = session.get('user_nombre') or pedido.get('creado_por') or '-'
    fecha_entrega = pedido.get('fecha_entrega') or '-'
    observaciones = (pedido.get('observaciones') or '').strip()

    cuerpo = f"""Estimado proveedor,

Le solicitamos el suministro del pedido adjunto correspondiente a:

Establecimiento: {establecimiento}
Departamento: {departamento}
Responsable: {responsable}

Datos del pedido:
- Número: {numero_pedido}
- Fecha prevista de entrega: {fecha_entrega}
"""

    if observaciones:
        cuerpo += f"\nObservaciones:\n{observaciones}\n"

    cuerpo += """
Le rogamos revise el pedido y, en caso de falta de producto, sustituciones o cualquier incidencia, nos lo comunique antes de la entrega para poder gestionarlo correctamente.

Quedamos a la espera de su confirmación.

Un saludo,
"""
    cuerpo += f"{establecimiento}\nEl Racó ERP\n"

    html_pedido = render_template('compras/imprimir_pedido.html', pedido=pedido, lineas=lineas)
    pdf_bytes = None
    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_pedido, base_url=request.url_root).write_pdf()
    except Exception:
        pdf_bytes = None

    email_enviado = False
    conn = None
    try:
        mail_actual = _obtener_mail_actual()
        remitente = _asegurar_config_mail_desde_env()

        msg = Message(
            subject=f'Pedido {numero_pedido} - {establecimiento}',
            recipients=[email_proveedor],
            body=cuerpo,
            sender=remitente or None
        )

        if pdf_bytes:
            msg.attach(
                filename=f'pedido_{numero_pedido}.pdf',
                content_type='application/pdf',
                data=pdf_bytes
            )
        else:
            msg.html = html_pedido

        mail_actual.send(msg)
        email_enviado = True

        conn = get_db()
        cur = conn.cursor()
        _asegurar_estructura_compras(cur)
        conn.execute('PRAGMA busy_timeout = 5000')
        scope_sql, scope_params = _scope_departamento('pedidos', empresa_id)
        cur.execute(f"""
            UPDATE pedidos
            SET estado = 'enviado', fecha_envio = ?
            WHERE id = ?
              AND {scope_sql}
              AND estado IN ('autorizado', 'enviado')
        """, [datetime.now().strftime('%Y-%m-%d %H:%M:%S'), pedido_id, *scope_params])
        if cur.rowcount != 1:
            raise ValueError('El pedido ha cambiado o ya no es accesible.')
        _registrar_auditoria_compra(
            cur,
            empresa_id,
            'pedido_enviado_email',
            referencia_tipo='pedido',
            referencia_id=pedido_id,
            detalle=f'Pedido enviado por email a {email_proveedor}',
            departamento_id=pedido_row['departamento_id'],
            proveedor_id=pedido_row['proveedor_id'],
            estado_anterior=pedido_row['estado'],
            estado_nuevo='enviado',
        )
        conn.commit()
        conn.close()
        conn = None

        flash('Email enviado al proveedor con texto profesional y pedido adjunto.', 'success')
    except Exception as e:
        if conn is not None:
            conn.rollback()
            conn.close()
        if email_enviado:
            current_app.logger.exception('El correo del pedido %s se envio, pero no se pudo registrar su estado.', pedido_id)
            flash('El correo se ha entregado al servicio de envio, pero no se pudo registrar el estado. No lo reenvies sin comprobarlo con el proveedor.', 'warning')
        else:
            flash(f'No se pudo completar el envio del correo: {str(e)}', 'danger')

    return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

def _generar_lote_interno(producto_id, fecha_recepcion):
    fecha_txt = (fecha_recepcion or datetime.now().strftime("%Y-%m-%d")).replace('-', '')
    return f"INT-{producto_id}-{fecha_txt}-{datetime.now().strftime('%H%M%S%f')[:10]}"



def _asegurar_columna_recepcion_stock_aplicado(cursor):
    """Añade stock_aplicado si no existe. Evita duplicar entradas de stock al validar."""
    columnas = [row[1] for row in cursor.execute("PRAGMA table_info(recepciones)").fetchall()]
    if 'stock_aplicado' not in columnas:
        cursor.execute("ALTER TABLE recepciones ADD COLUMN stock_aplicado INTEGER DEFAULT 0")


def _crear_incidencia_recepcion(cursor, empresa_id, recepcion_id, usuario, tipo_txt, descripcion_txt, gravedad='media'):
    descripcion_final = f"{tipo_txt}: {descripcion_txt}".strip(': ')
    ensure_incidencias_context_schema(cursor.connection)
    recepcion_ctx = cursor.execute(
        """
        SELECT r.departamento_id, d.nombre AS departamento_nombre
        FROM recepciones r
        LEFT JOIN departamentos d ON d.id = r.departamento_id
        WHERE r.id = ? AND r.empresa_id = ?
        """,
        (recepcion_id, empresa_id),
    ).fetchone()
    departamento_id = recepcion_ctx["departamento_id"] if recepcion_ctx else None
    ubicacion = f"Recepcion compras #{recepcion_id}"
    cursor.execute("""
        INSERT INTO incidencias
        (tipo, referencia_id, usuario, descripcion, gravedad, estado,
         origen_modulo, destino_modulo, categoria_operativa, departamento_id,
         ubicacion, requiere_mantenimiento, requiere_appcc, origen_tabla, origen_id, empresa_id)
        VALUES ('recepcion', ?, ?, ?, ?, 'abierta',
                'compras', 'compras', 'recepcion_compra', ?,
                ?, 0, 1, 'recepciones', ?, ?)
    """, (recepcion_id, usuario, descripcion_final, gravedad, departamento_id, ubicacion, recepcion_id, empresa_id))
    return cursor.lastrowid


def _aplicar_stock_recepcion(cursor, recepcion_id, empresa_id, usuario='sistema'):
    """
    Aplica stock/lotes cuando se confirma la recepcion operativa.
    Es idempotente: si stock_aplicado ya vale 1, no duplica entradas.
    """
    _asegurar_columna_recepcion_stock_aplicado(cursor)
    conn = cursor.connection
    scope_sql, scope_params, centro_id = _scope_recepciones_centro(conn, "r", empresa_id)

    recepcion = cursor.execute(f"""
        SELECT r.*, p.numero_pedido
        FROM recepciones r
        LEFT JOIN pedidos p ON p.id = r.pedido_id
        WHERE r.id = ?
          AND {scope_sql}
    """, [recepcion_id] + scope_params).fetchone()

    if not recepcion:
        raise ValueError('No se puede aplicar stock: la recepcion no es accesible en el centro activo.')

    if int(recepcion['stock_aplicado'] or 0) == 1:
        return 0

    producto_centro_sql = ""
    almacen_centro_sql = ""
    producto_centro_params = []
    almacen_centro_params = []
    columnas_productos = _columnas_tabla(cursor, "productos")
    columnas_almacenes = _columnas_tabla(cursor, "almacenes")
    if centro_id and "centro_id" in columnas_productos:
        producto_centro_sql = " AND (prod.centro_id IS NULL OR prod.centro_id = ?)"
        producto_centro_params.append(centro_id)
    if centro_id and "centro_id" in columnas_almacenes:
        almacen_centro_sql = " AND a.centro_id = ?"
        almacen_centro_params.append(centro_id)

    lineas = cursor.execute("""
        SELECT lr.*, prod.nombre AS producto_nombre
        FROM lineas_recepcion lr
        JOIN productos prod ON prod.id = lr.producto_id
        JOIN almacenes a ON a.id = lr.almacen_id
        WHERE lr.recepcion_id = ?
          AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
          AND prod.empresa_id = ?
          AND COALESCE(prod.activo, 1) = 1
          """ + producto_centro_sql + """
          AND a.empresa_id = ?
          AND COALESCE(a.activo, 1) = 1
          """ + almacen_centro_sql + """
          AND COALESCE(lr.cantidad_recibida, 0) > 0
          AND COALESCE(lr.aceptado, 1) = 1
          AND lr.almacen_id IS NOT NULL
    """, [recepcion_id, empresa_id, empresa_id] + producto_centro_params + [empresa_id] + almacen_centro_params).fetchall()

    aceptadas = cursor.execute("""
        SELECT COUNT(*) FROM lineas_recepcion
        WHERE recepcion_id = ? AND COALESCE(cantidad_recibida, 0) > 0
          AND COALESCE(aceptado, 1) = 1
    """, (recepcion_id,)).fetchone()[0]
    if len(lineas) != aceptadas:
        raise ValueError('No se puede aplicar stock: hay lineas aceptadas con producto o almacen no valido para este centro. Revisa la recepcion.')

    aplicadas = 0
    for linea in lineas:
        producto_id = linea['producto_id']
        almacen_id = linea['almacen_id']
        cantidad = float(linea['cantidad_recibida'] or 0)
        if cantidad <= 0:
            continue

        stock = cursor.execute("""
            SELECT id, cantidad
            FROM stock_almacen
            WHERE producto_id = ? AND almacen_id = ?
        """, (producto_id, almacen_id)).fetchone()

        if stock:
            cursor.execute("""
                UPDATE stock_almacen
                SET cantidad = COALESCE(cantidad, 0) + ?
                WHERE id = ?
            """, (cantidad, stock['id']))
        else:
            cursor.execute("""
                INSERT INTO stock_almacen (producto_id, almacen_id, cantidad, stock_minimo)
                VALUES (?, ?, ?, 0)
            """, (producto_id, almacen_id, cantidad))

        documento = recepcion['numero_albaran'] or recepcion['numero_pedido'] or f"RECEP-{recepcion_id}"
        cursor.execute("""
            INSERT INTO movimientos_stock (producto_id, almacen_id, tipo, cantidad, motivo, usuario, documento, empresa_id)
            VALUES (?, ?, 'entrada', ?, ?, ?, ?, ?)
        """, (producto_id, almacen_id, cantidad, 'Recepcion operativa', usuario, documento, empresa_id))

        coste_unitario = _to_float_safe(linea['precio_unitario'], 0.0)
        importe_total = _to_float_safe(linea['importe_total'], 0.0)
        if coste_unitario <= 0 and importe_total > 0 and cantidad > 0:
            coste_unitario = round(importe_total / cantidad, 6)
        coste_total = round(cantidad * coste_unitario, 6)

        codigo_lote = linea['lote'] or f"REC-{recepcion_id}-{producto_id}-{linea['id']}"
        origen_lote = 'proveedor' if linea['lote'] else 'interno'
        documento_origen = f"Recepción {recepcion_id}"
        if recepcion['numero_pedido']:
            documento_origen = f"Pedido {recepcion['numero_pedido']}"

        cursor.execute("""
            INSERT INTO lotes (
                codigo_lote, producto_id, cantidad_inicial, cantidad_actual, fecha_recepcion,
                fecha_caducidad, proveedor_id, almacen_id, temperatura_recepcion, lote_proveedor,
                origen_lote, albaran, estado, observaciones, empresa_id, responsable_recepcion,
                incidencia_id, documento_origen, notas_trazabilidad,
                coste_unitario, coste_total, moneda
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'activo', ?, ?, ?, ?, ?, ?, ?, ?, 'EUR')
        """, (
            codigo_lote, producto_id, cantidad, cantidad,
            recepcion['fecha_recepcion'] or datetime.now().strftime('%Y-%m-%d'),
            linea['fecha_caducidad'], recepcion['proveedor_id'], almacen_id,
            linea['temperatura'], linea['lote'], origen_lote, recepcion['numero_albaran'],
            recepcion['observaciones'], empresa_id, recepcion['recibido_por'] or usuario,
            linea['incidencia_id'], documento_origen,
            'Stock aplicado tras recepcion operativa; validacion economica independiente'
            + ('' if linea['lote'] else '; lote interno generado por ERP'),
            coste_unitario,
            coste_total,
        ))
        lote_id = cursor.lastrowid
        cursor.execute(
            "UPDATE lineas_recepcion SET lote_id = ? WHERE id = ?",
            (lote_id, linea['id'])
        )
        aplicadas += 1

    cursor.execute("UPDATE recepciones SET stock_aplicado = 1 WHERE id = ? AND empresa_id = ?", (recepcion_id, empresa_id))
    return aplicadas



def _ajustar_stock_almacen(cursor, producto_id, almacen_id, diferencia, empresa_id, usuario, documento, motivo):
    """Ajusta stock por diferencia. Positivo suma, negativo resta. Siempre deja movimiento trazable."""
    if not producto_id or not almacen_id:
        return

    diferencia = _to_float_safe(diferencia, 0.0)
    if not diferencia:
        return

    stock = cursor.execute("""
        SELECT id, cantidad
        FROM stock_almacen
        WHERE producto_id = ? AND almacen_id = ?
    """, (producto_id, almacen_id)).fetchone()

    if stock:
        stock_final = _to_float_safe(stock['cantidad'], 0.0) + diferencia
        if stock_final < 0:
            raise ValueError('El ajuste dejaria el stock en negativo.')
        cursor.execute("""
            UPDATE stock_almacen
            SET cantidad = ?
            WHERE id = ?
        """, (stock_final, stock['id']))
    else:
        if diferencia < 0:
            raise ValueError('No se puede registrar una salida sin stock previo.')
        cursor.execute("""
            INSERT INTO stock_almacen (producto_id, almacen_id, cantidad, stock_minimo)
            VALUES (?, ?, ?, 0)
        """, (producto_id, almacen_id, diferencia))

    tipo_mov = 'entrada' if diferencia > 0 else 'salida'
    cursor.execute("""
        INSERT INTO movimientos_stock (producto_id, almacen_id, tipo, cantidad, motivo, usuario, documento, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        producto_id,
        almacen_id,
        tipo_mov,
        abs(diferencia),
        motivo,
        usuario,
        documento,
        empresa_id
    ))


def _ajustar_lote_recepcion_linea(cursor, recepcion, linea, cantidad_nueva, almacen_nuevo, lote_nuevo, caducidad_nueva, temperatura_nueva):
    """Actualiza el lote creado por la recepción cuando se edita la línea."""
    producto_id = linea['producto_id']
    linea_id = linea['id']
    codigo_lote_anterior = linea['lote'] or f"REC-{recepcion['id']}-{producto_id}-{linea_id}"
    codigo_lote_nuevo = lote_nuevo or f"REC-{recepcion['id']}-{producto_id}-{linea_id}"
    origen_lote_nuevo = 'proveedor' if lote_nuevo else 'interno'

    lote = cursor.execute("""
        SELECT id
        FROM lotes
        WHERE producto_id = ?
          AND codigo_lote = ?
          AND empresa_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (producto_id, codigo_lote_anterior, recepcion['empresa_id'])).fetchone()

    if lote:
        cursor.execute("""
            UPDATE lotes
            SET codigo_lote = ?,
                cantidad_inicial = ?,
                cantidad_actual = ?,
                almacen_id = ?,
                fecha_caducidad = ?,
                temperatura_recepcion = ?,
                lote_proveedor = ?,
                origen_lote = ?
            WHERE id = ?
        """, (
            codigo_lote_nuevo,
            cantidad_nueva,
            cantidad_nueva,
            almacen_nuevo,
            caducidad_nueva,
            temperatura_nueva,
            lote_nuevo or None,
            origen_lote_nuevo,
            lote['id']
        ))


def _registrar_cantidad_pendiente(cursor, linea_id, pedido_id, cantidad):
    """Reserva cantidad aun pendiente dentro de la transaccion de recepcion."""
    if not math.isfinite(cantidad) or cantidad <= 0:
        raise ValueError('La cantidad recibida debe ser un numero positivo y finito.')
    resultado = cursor.execute("""
        UPDATE lineas_pedido
        SET cantidad_recibida = COALESCE(cantidad_recibida, 0) + ?
        WHERE id = ? AND pedido_id = ?
          AND ? <= COALESCE(cantidad_solicitada, 0) - COALESCE(cantidad_recibida, 0) + 0.000000001
    """, (cantidad, linea_id, pedido_id, cantidad))
    if resultado.rowcount != 1:
        raise ValueError('La cantidad pendiente ha cambiado o es insuficiente. Recarga el pedido y revisa la recepcion.')


def _recalcular_cantidad_recibida_pedido(cursor, pedido_id):
    """Recalcula lineas_pedido.cantidad_recibida desde las recepciones reales del pedido."""
    if not pedido_id:
        return

    duplicado = cursor.execute("""
        SELECT 1 FROM lineas_pedido
        WHERE pedido_id = ?
        GROUP BY producto_id HAVING COUNT(*) > 1
        LIMIT 1
    """, (pedido_id,)).fetchone()
    if duplicado:
        raise ValueError('No se puede recalcular: el pedido tiene varias lineas del mismo producto y falta asociar cada recepcion a su linea. Revisa el pedido antes de modificar la recepcion.')

    lineas = cursor.execute("""
        SELECT id, producto_id
        FROM lineas_pedido
        WHERE pedido_id = ?
    """, (pedido_id,)).fetchall()

    for linea in lineas:
        total = cursor.execute("""
            SELECT SUM(COALESCE(lr.cantidad_recibida, 0)) AS total
            FROM lineas_recepcion lr
            JOIN recepciones r ON r.id = lr.recepcion_id
            WHERE r.pedido_id = ?
              AND lr.producto_id = ?
              AND COALESCE(lr.aceptado, 1) = 1
        """, (pedido_id, linea['producto_id'])).fetchone()
        cursor.execute("""
            UPDATE lineas_pedido
            SET cantidad_recibida = ?
            WHERE id = ?
        """, (_to_float_safe(total['total'], 0.0) if total else 0.0, linea['id']))


def _procesar_linea_recepcion(cursor, empresa_id, pedido, recepcion_id, linea, form_data, usuario, numero_albaran, fecha_recepcion, observaciones_generales):
    pendiente = float(linea['cantidad_solicitada'] or 0) - float(linea['cantidad_recibida'] or 0)
    if pendiente <= 0:
        return False

    cantidad_recibida = float(form_data.get(f"cantidad_{linea['id']}", 0) or 0)
    if cantidad_recibida <= 0:
        return False

    if cantidad_recibida > pendiente:
        cantidad_recibida = pendiente

    lote_proveedor = (form_data.get(f"lote_{linea['id']}") or '').strip()
    fecha_caducidad = (form_data.get(f"caducidad_{linea['id']}") or '').strip() or None
    temperatura_txt = (form_data.get(f"temperatura_{linea['id']}") or '').strip()
    precio_txt = (form_data.get(f"precio_{linea['id']}") or '').strip()
    almacen_txt = (form_data.get(f"almacen_{linea['id']}") or '').strip()
    num_envases = _to_int_or_none(form_data.get(f"envases_{linea['id']}")) or 1
    if num_envases < 1:
        num_envases = 1
    incidencia_marcada = form_data.get(f"incidencia_{linea['id']}")
    incidencia_tipo = (form_data.get(f"incidencia_tipo_{linea['id']}") or '').strip()
    incidencia_texto = (form_data.get(f"incidencia_texto_{linea['id']}") or '').strip()

    temperatura = float(temperatura_txt) if temperatura_txt else None
    precio_unitario = float(precio_txt) if precio_txt else float(linea['precio_unitario'] or 0)
    almacen_id = int(almacen_txt) if almacen_txt else None
    importe_total = round(cantidad_recibida * precio_unitario, 2)

    incidencia_id = None
    gravedad = 'media'
    if incidencia_marcada:
        if not incidencia_tipo:
            incidencia_tipo = 'Incidencia de recepción'
        if not incidencia_texto:
            incidencia_texto = f"Incidencia detectada al recibir {linea['nombre']}"
        if incidencia_tipo == 'Temperatura incorrecta':
            gravedad = 'alta'
        incidencia_id = _crear_incidencia_recepcion(cursor, empresa_id, recepcion_id, usuario, incidencia_tipo, incidencia_texto, gravedad=gravedad)

    cursor.execute("""
        INSERT INTO lineas_recepcion (
            recepcion_id, producto_id, cantidad_recibida, lote, fecha_caducidad,
            temperatura, almacen_id, precio_unitario, importe_total, aceptado,
            motivo_rechazo, incidencia_id, num_envases
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        recepcion_id, linea['producto_id'], cantidad_recibida, lote_proveedor or None,
        fecha_caducidad, temperatura, almacen_id, precio_unitario, importe_total,
        0 if incidencia_marcada else 1,
        incidencia_texto if incidencia_marcada else None,
        incidencia_id,
        num_envases
    ))
    linea_recepcion_id = cursor.lastrowid

    if incidencia_marcada:
        return True

    cursor.execute("""
        UPDATE lineas_pedido
        SET cantidad_recibida = COALESCE(cantidad_recibida, 0) + ?
        WHERE id = ?
    """, (cantidad_recibida, linea['id']))

    if almacen_id:
        existe = cursor.execute("""
            SELECT id FROM stock_almacen
            WHERE producto_id = ? AND almacen_id = ?
        """, (linea['producto_id'], almacen_id)).fetchone()

        if existe:
            cursor.execute("""
                UPDATE stock_almacen
                SET cantidad = COALESCE(cantidad, 0) + ?
                WHERE producto_id = ? AND almacen_id = ?
            """, (cantidad_recibida, linea['producto_id'], almacen_id))
        else:
            cursor.execute("""
                INSERT INTO stock_almacen (producto_id, almacen_id, cantidad)
                VALUES (?, ?, ?)
            """, (linea['producto_id'], almacen_id, cantidad_recibida))

        cursor.execute("""
            INSERT INTO movimientos_stock (producto_id, almacen_id, tipo, cantidad, motivo, usuario, documento, empresa_id)
            VALUES (?, ?, 'entrada', ?, ?, ?, ?, ?)
        """, (
            linea['producto_id'], almacen_id, cantidad_recibida,
            f"Recepción pedido {pedido['numero_pedido']}", usuario,
            numero_albaran or pedido['numero_pedido'], empresa_id
        ))

    codigo_lote = lote_proveedor or _generar_lote_interno(linea['producto_id'], fecha_recepcion)
    origen_lote = 'proveedor' if lote_proveedor else 'interno'
    notas_trazabilidad = observaciones_generales or f"Recepción desde pedido {pedido['numero_pedido']}"
    if not lote_proveedor:
        notas_trazabilidad = f"{notas_trazabilidad}; lote interno generado por ERP"
    coste_unitario = _to_float_safe(precio_unitario, 0.0)
    coste_total = round(cantidad_recibida * coste_unitario, 6)
    cursor.execute("""
        INSERT INTO lotes (
            codigo_lote, producto_id, cantidad_inicial, cantidad_actual, fecha_recepcion,
            fecha_caducidad, proveedor_id, almacen_id, temperatura_recepcion,
            lote_proveedor, origen_lote, albaran, observaciones, empresa_id, responsable_recepcion,
            incidencia_id, documento_origen, notas_trazabilidad,
            coste_unitario, coste_total, moneda
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'EUR')
    """, (
        codigo_lote, linea['producto_id'], cantidad_recibida, cantidad_recibida,
        fecha_recepcion or datetime.now().strftime('%Y-%m-%d'), fecha_caducidad,
        pedido['proveedor_id'], almacen_id, temperatura, lote_proveedor or None,
        origen_lote, numero_albaran or None, observaciones_generales or None, empresa_id, usuario,
        incidencia_id, f"Pedido {pedido['id']}", notas_trazabilidad,
        coste_unitario, coste_total
    ))
    lote_id = cursor.lastrowid
    cursor.execute(
        "UPDATE lineas_recepcion SET lote_id = ? WHERE id = ?",
        (lote_id, linea_recepcion_id),
    )

    return True


def _obtener_resumen_recepcion_por_producto(conn, pedido_id):
    rows = conn.execute("""
        SELECT
            lr.*,
            r.fecha_recepcion,
            r.numero_albaran,
            r.recibido_por,
            a.nombre AS almacen_nombre,
            prod.nombre AS producto_nombre
        FROM lineas_recepcion lr
        JOIN recepciones r ON lr.recepcion_id = r.id
        JOIN productos prod ON lr.producto_id = prod.id
        LEFT JOIN almacenes a ON lr.almacen_id = a.id
        WHERE r.pedido_id = ?
        ORDER BY r.id DESC, lr.id DESC
    """, (pedido_id,)).fetchall()

    resumen = {}
    for row in rows:
        pid = row['producto_id']
        resumen.setdefault(pid, []).append(row)
    return resumen


def _obtener_stock_actual_por_producto(conn, producto_ids):
    if not producto_ids:
        return {}
    placeholders = ','.join(['?'] * len(producto_ids))
    rows = conn.execute(f"""
        SELECT producto_id, SUM(COALESCE(cantidad, 0)) AS total_stock
        FROM stock_almacen
        WHERE producto_id IN ({placeholders})
        GROUP BY producto_id
    """, tuple(producto_ids)).fetchall()
    return {row['producto_id']: row['total_stock'] for row in rows}


def _obtener_lotes_por_producto(conn, pedido_id):
    rows = conn.execute("""
        SELECT
            l.*,
            a.nombre AS almacen_nombre,
            prod.nombre AS producto_nombre
        FROM lotes l
        JOIN productos prod ON l.producto_id = prod.id
        LEFT JOIN almacenes a ON l.almacen_id = a.id
        WHERE l.documento_origen = ?
        ORDER BY l.id DESC
    """, (f"Pedido {pedido_id}",)).fetchall()
    # compatibilidad con datos anteriores generados usando numero_pedido
    if not rows:
        rows = conn.execute("""
            SELECT
                l.*,
                a.nombre AS almacen_nombre,
                prod.nombre AS producto_nombre
            FROM lotes l
            JOIN productos prod ON l.producto_id = prod.id
            LEFT JOIN almacenes a ON l.almacen_id = a.id
            WHERE l.documento_origen LIKE 'Pedido %'
            ORDER BY l.id DESC
        """).fetchall()
    resumen = {}
    for row in rows:
        resumen.setdefault(row['producto_id'], []).append(row)
    return resumen


@compras_bp.route('/recepcion', methods=['GET', 'POST'])
@login_required
def recepcion_pedido():
    if request.method == "POST":
        conn = get_db()
        empresa_id = session.get('user_empresa_id', 1)
        centro_id = _centro_activo_id()
        bloqueo = _requiere_recepcion_compras(conn, destino='compras.validar_recepciones')
        if bloqueo:
            return bloqueo
        conn.close()
        if not centro_id:
            flash('Selecciona un centro antes de recepcionar pedidos.', 'warning')
            return redirect(url_for('rrhh.centro_empresa_general'))
        numero_pedido = (request.form.get("numero_pedido") or '').strip()
        if numero_pedido:
            return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

    return render_template("compras/buscar_recepcion.html")


@compras_bp.route('/recepcion/<string:numero_pedido>', methods=['GET', 'POST'])
@login_required
def recibir_pedido(numero_pedido):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    usuario = (session.get('user_nombre') or 'admin').strip()
    ver_precios = _usuario_puede_ver_precios_compras()
    modo_tablet = request.args.get('tablet') == '1' or request.form.get('modo_tablet') == '1'
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    scope_sql, scope_params = _scope_departamento('p', empresa_id)
    pedido = conn.execute(f"""
        SELECT p.*, pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        LEFT JOIN departamentos d ON d.id = p.departamento_id
        WHERE p.numero_pedido = ?
          AND {scope_sql}
    """, [numero_pedido, *scope_params]).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    bloqueo = _requiere_gestion_compras(conn, pedido['departamento_id'])
    if bloqueo:
        return bloqueo

    lineas = conn.execute("""
        SELECT l.*, prod.nombre, prod.unidad, prod.codigo, prod.tipo_conservacion
        FROM lineas_pedido l
        JOIN productos prod ON l.producto_id = prod.id
        WHERE l.pedido_id = ? AND prod.empresa_id = ?
        ORDER BY prod.nombre
    """, (pedido['id'], empresa_id)).fetchall()

    almacenes = _almacenes_visibles_compras(conn, empresa_id, pedido['departamento_id'])
    almacenes_ids = {int(a['id']) for a in almacenes}

    total_pendiente = sum(
        max(_to_float_safe(l['cantidad_solicitada']) - _to_float_safe(l['cantidad_recibida']), 0.0)
        for l in lineas
    )
    estado_pedido = (pedido['estado'] or '').strip().lower()

    if estado_pedido not in ('enviado', 'recibido_parcial', 'recibido', 'cerrado', 'cancelado'):
        conn.close()
        flash('Antes de recepcionar, el pedido debe estar enviado al proveedor.', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido['id']))

    if request.method == 'GET' and (estado_pedido in ('recibido', 'cerrado', 'cancelado') or total_pendiente <= 0):
        conn.close()
        flash('Este pedido ya no tiene cantidades pendientes para recepcionar.', 'info')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido['id']))

    if request.method == 'POST':
        accion = (request.form.get('accion_recepcion') or 'guardar').strip().lower()
        numero_albaran = (request.form.get('numero_albaran') or '').strip()
        fecha_recepcion = (request.form.get('fecha_recepcion') or datetime.now().strftime('%Y-%m-%d')).strip()
        recibido_por = (request.form.get('recibido_por') or usuario).strip()
        observaciones = (request.form.get('observaciones') or '').strip()
        documento_id_form = _to_int_or_none(request.form.get('documento_id'))
        imprimir_etiquetas = request.form.get('imprimir_etiquetas') == '1'

        if accion == 'leer':
            documento = request.files.get('documento_compra')
            if not documento or not (documento.filename or '').strip():
                conn.close()
                flash('Selecciona un albarán, foto o PDF para leer.', 'warning')
                return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

            try:
                cur = conn.cursor()
                _asegurar_estructura_compras(cur)
                documento_id = guardar_documento_compra_en_cursor(
                    cur,
                    documento,
                    proveedor_id=pedido['proveedor_id'],
                    pedido_id=pedido['id'],
                    recepcion_id=None,
                    tipo_documento='albaran',
                    origen_subida='lectura_recepcion_pedido'
                )

                resultado = procesar_documento_compra_ocr_en_cursor(
                    cur,
                    documento_id,
                    empresa_id,
                    proveedor_id=pedido['proveedor_id']
                )

                texto_ocr = resultado.get('texto') or ''
                albaran_detectado = detectar_numero_albaran_desde_texto(texto_ocr) or numero_albaran

                lineas_doc = cur.execute("""
                    SELECT *
                    FROM documento_compra_lineas_detectadas
                    WHERE documento_id = ?
                    ORDER BY id
                """, (documento_id,)).fetchall()

                incidencias_preview = []
                cantidades_ocr = {}

                for ld in lineas_doc:
                    producto_id = ld['producto_id']
                    cantidad_doc = _to_float_safe(ld['cantidad_detectada'])

                    if not producto_id:
                        incidencias_preview.append({
                            'tipo': 'Producto no reconocido',
                            'descripcion': ld['descripcion_detectada'] or ''
                        })
                        continue

                    cantidades_ocr[producto_id] = cantidades_ocr.get(producto_id, 0.0) + cantidad_doc

                    pedido_linea = cur.execute("""
                        SELECT lp.*, prod.nombre
                        FROM lineas_pedido lp
                        JOIN productos prod ON prod.id = lp.producto_id
                        WHERE lp.pedido_id = ? AND lp.producto_id = ?
                        LIMIT 1
                    """, (pedido['id'], producto_id)).fetchone()

                    if not pedido_linea:
                        incidencias_preview.append({
                            'tipo': 'Producto no pedido',
                            'descripcion': ld['descripcion_detectada'] or ''
                        })
                        continue

                    pendiente = max(
                        _to_float_safe(pedido_linea['cantidad_solicitada']) - _to_float_safe(pedido_linea['cantidad_recibida']),
                        0.0
                    )
                    if cantidad_doc < pendiente:
                        incidencias_preview.append({
                            'tipo': 'Falta unidades',
                            'descripcion': f"{pedido_linea['nombre']}: pedido pendiente {pendiente} / albarán {cantidad_doc}"
                        })
                    elif cantidad_doc > pendiente:
                        incidencias_preview.append({
                            'tipo': 'Exceso unidades',
                            'descripcion': f"{pedido_linea['nombre']}: pedido pendiente {pendiente} / albarán {cantidad_doc}"
                        })

                conn.commit()
                flash(
                    f"OCR finalizado: {resultado.get('lineas', 0)} línea(s), "
                    f"{resultado.get('asociadas', 0)} asociada(s), "
                    f"{len(incidencias_preview)} aviso(s). Revisa antes de guardar.",
                    'success'
                )
                conn.close()
                return render_template(
                    'compras/recibir_pedido.html',
                    pedido=pedido,
                    lineas=lineas,
                    almacenes=almacenes,
                    now=datetime.now(),
                    documento_id=documento_id,
                    albaran_detectado=albaran_detectado,
                    texto_ocr=texto_ocr,
                    incidencias_preview=incidencias_preview,
                    cantidades_ocr=cantidades_ocr,
                    ver_precios=ver_precios,
                    modo_tablet=modo_tablet
                )
            except Exception as e:
                conn.rollback()
                conn.close()
                flash(f'Error leyendo documento: {str(e)}', 'danger')
                return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

        if estado_pedido in ('recibido', 'cerrado', 'cancelado') or total_pendiente <= 0:
            conn.close()
            flash('Este pedido ya está recibido/cerrado o no tiene cantidades pendientes.', 'warning')
            return redirect(url_for('compras.ver_pedido', pedido_id=pedido['id']))

        cerrar_pedido_final = accion == 'guardar_cerrar_pendiente'
        recibir_todo = accion == 'recibir_todo'

        try:
            cur = conn.cursor()
            _asegurar_estructura_compras(cur)
            documento_adjunto_id = documento_id_form
            if documento_adjunto_id:
                documento_ok = cur.execute("""
                    SELECT id
                    FROM documentos_compra
                    WHERE id = ?
                      AND empresa_id = ?
                      AND (proveedor_id = ? OR proveedor_id IS NULL)
                      AND (pedido_id = ? OR pedido_id IS NULL)
                """, (documento_adjunto_id, empresa_id, pedido['proveedor_id'], pedido['id'])).fetchone()
                if not documento_ok:
                    documento_adjunto_id = None

            cur.execute("""
                INSERT INTO recepciones (
                    pedido_id, proveedor_id, departamento_id, departamento_receptor_id, fecha_recepcion,
                    numero_albaran, observaciones, recibido_por, empresa_id,
                    tipo_recepcion, validada_operativamente, validada_economicamente,
                    stock_aplicado, documento_adjunto_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pedido', 1, 0, 0, ?)
            """, (
                pedido['id'],
                pedido['proveedor_id'],
                pedido['departamento_id'],
                pedido['departamento_id'],
                fecha_recepcion,
                numero_albaran,
                observaciones,
                recibido_por,
                empresa_id,
                documento_adjunto_id
            ))
            recepcion_id = cur.lastrowid

            if documento_adjunto_id:
                cur.execute("""
                    UPDATE documentos_compra
                    SET recepcion_id = ?, proveedor_id = ?, pedido_id = ?
                    WHERE id = ? AND empresa_id = ?
                """, (recepcion_id, pedido['proveedor_id'], pedido['id'], documento_adjunto_id, empresa_id))
            else:
                documento = request.files.get('documento_compra')
                if documento and (documento.filename or '').strip():
                    documento_adjunto_id = guardar_documento_compra_en_cursor(
                        cur,
                        documento,
                        proveedor_id=pedido['proveedor_id'],
                        pedido_id=pedido['id'],
                        recepcion_id=recepcion_id,
                        tipo_documento='albaran',
                        origen_subida='recepcion_pedido'
                    )
                    if documento_adjunto_id:
                        procesar_documento_compra_ocr_en_cursor(
                            cur,
                            documento_adjunto_id,
                            empresa_id,
                            proveedor_id=pedido['proveedor_id']
                        )
                        if not numero_albaran:
                            doc_row = cur.execute(
                                'SELECT texto_extraido FROM documentos_compra WHERE id = ? AND empresa_id = ?',
                                (documento_adjunto_id, empresa_id)
                            ).fetchone()
                            albaran_detectado = detectar_numero_albaran_desde_texto(doc_row['texto_extraido'] if doc_row else '')
                            if albaran_detectado:
                                numero_albaran = albaran_detectado
                                cur.execute('UPDATE recepciones SET numero_albaran = ? WHERE id = ?', (numero_albaran, recepcion_id))
                        cur.execute('UPDATE recepciones SET documento_adjunto_id = ? WHERE id = ?', (documento_adjunto_id, recepcion_id))

            precios_ocr_por_producto = {}
            if documento_adjunto_id:
                try:
                    filas_ocr_precio = cur.execute("""
                        SELECT producto_id, precio_detectado
                        FROM documento_compra_lineas_detectadas
                        WHERE documento_id = ?
                          AND producto_id IS NOT NULL
                          AND precio_detectado IS NOT NULL
                          AND precio_detectado > 0
                        ORDER BY id
                    """, (documento_adjunto_id,)).fetchall()
                    for fila_precio in filas_ocr_precio:
                        pid_ocr = fila_precio['producto_id']
                        precio_ocr = _to_float_safe(fila_precio['precio_detectado'], None)
                        if pid_ocr and precio_ocr and precio_ocr > 0 and pid_ocr not in precios_ocr_por_producto:
                            precios_ocr_por_producto[pid_ocr] = precio_ocr
                except Exception:
                    precios_ocr_por_producto = {}

            lineas_guardadas = 0
            incidencias_guardadas = 0

            for l in lineas:
                pendiente = max(_to_float_safe(l['cantidad_solicitada']) - _to_float_safe(l['cantidad_recibida']), 0.0)
                if pendiente <= 0:
                    continue

                cantidad_raw = request.form.get(f'cantidad_{l["id"]}')
                if recibir_todo and not (cantidad_raw or '').strip():
                    cantidad_raw = str(pendiente)
                cantidad = _to_float_safe(cantidad_raw)
                if not math.isfinite(cantidad) or cantidad < 0:
                    conn.rollback()
                    conn.close()
                    flash('La cantidad recibida debe ser un numero valido y no negativo.', 'warning')
                    return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))
                if cantidad > pendiente and not cerrar_pedido_final:
                    cantidad = pendiente

                estado_appcc = (request.form.get(f'estado_{l["id"]}') or 'ok').strip()
                incidencia_texto = (request.form.get(f'incidencia_texto_{l["id"]}') or '').strip()
                tiene_incidencia = estado_appcc != 'ok'

                if cantidad <= 0 and not tiene_incidencia:
                    continue

                almacen_id = _to_int_or_none(request.form.get(f'almacen_{l["id"]}'))
                if cantidad > 0 and not almacen_id:
                    conn.rollback()
                    conn.close()
                    flash(f'Debes indicar almacén en la línea: {l["nombre"]}.', 'warning')
                    return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

                if almacen_id and almacen_id not in almacenes_ids:
                    conn.rollback()
                    conn.close()
                    flash('No puedes recepcionar material en un almacen de otro departamento.', 'warning')
                    return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

                lote = (request.form.get(f'lote_{l["id"]}') or '').strip()
                num_envases = _to_int_or_none(request.form.get(f'envases_{l["id"]}')) or 1
                if num_envases < 1:
                    num_envases = 1
                caducidad = (request.form.get(f'caducidad_{l["id"]}') or '').strip() or None
                temperatura_raw = (request.form.get(f'temperatura_{l["id"]}') or '').strip()
                temperatura = _to_float_safe(temperatura_raw, 0.0) if temperatura_raw else None
                precio_form = _to_float_safe(request.form.get(f'precio_{l["id"]}'), None)
                precio_pedido = _to_float_safe(l['precio_unitario'], None)
                precio_ocr = precios_ocr_por_producto.get(l['producto_id'])
                precio_unitario = precio_form if precio_form and precio_form > 0 else (
                    precio_ocr if precio_ocr and precio_ocr > 0 else precio_pedido
                )

                incidencia_id = None
                aceptado = 1
                motivo_rechazo = None

                if cantidad < pendiente and cerrar_pedido_final and not incidencia_texto:
                    tiene_incidencia = True
                    estado_appcc = 'falta_unidades'
                    incidencia_texto = f"Pedido cerrado con faltante. Pendiente {pendiente}, recibido {cantidad}."

                if tiene_incidencia:
                    tipo_map = {
                        'falta_unidades': 'Falta de unidades',
                        'falta_peso': 'Falta de peso',
                        'rotura': 'Rotura',
                        'mal_estado': 'Mal estado',
                        'temperatura_incorrecta': 'Temperatura incorrecta'
                    }
                    incidencia_tipo = tipo_map.get(estado_appcc, 'Incidencia en recepción')
                    if not incidencia_texto:
                        incidencia_texto = f'{incidencia_tipo} en {l["nombre"]} durante recepción {numero_pedido}.'
                    motivo_rechazo = incidencia_texto
                    if estado_appcc in ('rotura', 'mal_estado', 'temperatura_incorrecta'):
                        aceptado = 0
                    incidencia_id = _crear_incidencia_recepcion(
                        cur,
                        empresa_id,
                        recepcion_id,
                        recibido_por,
                        incidencia_tipo,
                        incidencia_texto,
                        gravedad='alta' if aceptado == 0 else 'media'
                    )
                    incidencias_guardadas += 1

                importe_total = round(cantidad * precio_unitario, 2) if precio_unitario else None
                cur.execute("""
                    INSERT INTO lineas_recepcion (
                        recepcion_id, producto_id, cantidad_recibida, lote,
                        fecha_caducidad, temperatura, almacen_id, precio_unitario,
                        importe_total, aceptado, motivo_rechazo, incidencia_id,
                        empresa_id, num_envases
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    recepcion_id,
                    l['producto_id'],
                    cantidad,
                    lote or None,
                    caducidad,
                    temperatura,
                    almacen_id,
                    precio_unitario,
                    importe_total,
                    aceptado,
                    motivo_rechazo,
                    incidencia_id,
                    empresa_id,
                    num_envases
                ))
                lineas_guardadas += 1

                if cantidad > 0 and aceptado == 1:
                    _registrar_cantidad_pendiente(cur, l['id'], pedido['id'], cantidad)

            if lineas_guardadas == 0:
                conn.rollback()
                conn.close()
                flash('No se ha guardado ninguna línea ni incidencia.', 'warning')
                return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

            if cerrar_pedido_final:
                cur.execute("""
                    UPDATE pedidos
                    SET estado = 'cerrado', cerrado = 1, fecha_cierre = datetime('now'),
                        estado_recepcion = 'cerrado', fecha_recepcion = ?
                    WHERE id = ? AND empresa_id = ?
                """, (fecha_recepcion, pedido['id'], empresa_id))
            else:
                _recalcular_estado_pedido(cur, pedido['id'], empresa_id)
                cur.execute("""
                    UPDATE pedidos
                    SET fecha_recepcion = ?, estado_recepcion = estado
                    WHERE id = ? AND empresa_id = ?
                """, (fecha_recepcion, pedido['id'], empresa_id))

            aplicadas = _aplicar_stock_recepcion(cur, recepcion_id, empresa_id, recibido_por)
            _registrar_auditoria_compra(
                cur,
                empresa_id,
                'recepcion_guardada',
                referencia_tipo='recepcion',
                referencia_id=recepcion_id,
                detalle=f'Recepcion desde pedido {pedido["numero_pedido"]}: {lineas_guardadas} lineas, {incidencias_guardadas} incidencias, stock {aplicadas}',
                departamento_id=pedido['departamento_id'],
                proveedor_id=pedido['proveedor_id'],
                estado_nuevo='recepcion_operativa',
                usuario=recibido_por,
            )
            conn.commit()
            conn.close()

            mensaje = f'Recepción guardada: {lineas_guardadas} línea(s), {incidencias_guardadas} incidencia(s), stock aplicado en {aplicadas} línea(s).'
            if cerrar_pedido_final:
                mensaje += ' Pedido cerrado manualmente.'
            flash(mensaje, 'success')
            if imprimir_etiquetas:
                return redirect(url_for('compras.etiquetas_recepcion', recepcion_id=recepcion_id))
            return redirect(url_for('compras.validar_recepciones'))

        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f'Error registrando recepción: {str(e)}', 'danger')
            return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

    conn.close()
    return render_template(
        'compras/recibir_pedido.html',
        pedido=pedido,
        lineas=lineas,
        almacenes=almacenes,
        now=datetime.now(),
        documento_id=None,
        albaran_detectado=None,
        texto_ocr='',
        incidencias_preview=[],
        cantidades_ocr={},
        ver_precios=ver_precios,
        modo_tablet=modo_tablet
    )

@compras_bp.route('/recepcion/<string:numero_pedido>/conformidad')
@login_required
def hoja_conformidad(numero_pedido):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    scope_sql, scope_params, centro_activo_id = _scope_recepciones_centro(conn, 'r', empresa_id)
    recepcion = conn.execute(f"""
        SELECT r.*, p.nombre as proveedor_nombre, p.nif, p.email, p.telefono,
               ped.numero_pedido, ped.fecha_pedido
        FROM recepciones r
        JOIN proveedores p ON r.proveedor_id = p.id
        JOIN pedidos ped ON r.pedido_id = ped.id
        WHERE ped.numero_pedido = ?
          AND ped.empresa_id = ?
          AND {scope_sql}
        ORDER BY r.fecha_recepcion DESC, r.id DESC
        LIMIT 1
    """, [numero_pedido, empresa_id, *scope_params]).fetchone()

    if not recepcion:
        conn.close()
        flash('No existe recepción registrada para este pedido.', 'warning')
        return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

    lineas = conn.execute("""
        SELECT lr.*, prod.nombre as producto_nombre, prod.codigo, prod.unidad
        FROM lineas_recepcion lr
        JOIN productos prod ON lr.producto_id = prod.id
        WHERE lr.recepcion_id = ? AND prod.empresa_id = ?
    """, (recepcion['id'], empresa_id)).fetchall()

    conn.close()
    return render_template('compras/hoja_conformidad.html', recepcion=recepcion, lineas=lineas, now=datetime.now())


@compras_bp.route('/recepcion/<string:numero_pedido>/incidencias')
@login_required
def hoja_incidencias_recepcion(numero_pedido):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    scope_sql, scope_params = _scope_departamento('p', empresa_id)
    pedido = conn.execute(f"""
        SELECT p.*, pr.nombre as proveedor_nombre
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        WHERE p.numero_pedido = ?
          AND {scope_sql}
    """, [numero_pedido, *scope_params]).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado', 'danger')
        return redirect(url_for('compras.recepcion_pedido'))

    incidencias = conn.execute("""
        SELECT i.*, lr.producto_id, prod.nombre as producto_nombre, r.fecha_recepcion, r.numero_albaran
        FROM incidencias i
        LEFT JOIN lineas_recepcion lr ON lr.incidencia_id = i.id
        LEFT JOIN recepciones r ON lr.recepcion_id = r.id
        LEFT JOIN productos prod ON lr.producto_id = prod.id
        WHERE r.pedido_id = ? AND i.empresa_id = ?
        ORDER BY i.id DESC
    """, (pedido['id'], empresa_id)).fetchall()

    conn.close()
    return render_template('compras/hoja_incidencias_recepcion.html', pedido=pedido, incidencias=incidencias, now=datetime.now())

# ============================================
# COMPRAS - OPCIN A (Pedido por lista de productos)
# ============================================
@compras_bp.route('/pedidos/nuevo-tipo-a', methods=['GET', 'POST'])
@login_required
def nuevo_pedido_tipo_a():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    bloqueo = _requiere_gestion_compras(conn, destino='compras.lista_pedidos')
    if bloqueo:
        return bloqueo

    if request.method == "POST":
        departamento_id = request.form.get("departamento_id")
        productos_seleccionados = request.form.getlist("producto_id[]")
        cantidades = request.form.getlist("cantidad[]")
        fecha_entrega = request.form.get("fecha_entrega", "")
        observaciones = request.form.get("observaciones", "")

        if not departamento_id:
            conn.close()
            flash('Debes seleccionar departamento.', 'warning')
            return redirect(url_for('compras.nuevo_pedido_tipo_a'))

        if not _departamento_form_permitido(departamento_id):
            conn.close()
            flash('No puedes crear pedidos para otro departamento.', 'warning')
            return redirect(url_for('compras.nuevo_pedido_tipo_a'))

        pedidos_por_proveedor = {}

        for i in range(len(productos_seleccionados)):
            producto_raw = productos_seleccionados[i] if i < len(productos_seleccionados) else ''
            cantidad_raw = cantidades[i] if i < len(cantidades) else ''
            if not producto_raw or not cantidad_raw:
                continue

            producto_id = int(producto_raw)
            cantidad = _to_float_safe(cantidad_raw, 0.0)
            if cantidad <= 0:
                continue

            proveedor = conn.execute("""
                SELECT proveedor_id,
                       COALESCE(precio_vigente, precio_ultimo, precio_compra, precio, 0) AS precio
                FROM productos_proveedores
                WHERE producto_id = ?
                  AND COALESCE(activo, 1) = 1
                ORDER BY COALESCE(es_principal, 0) DESC, id DESC
                LIMIT 1
            """, (producto_id,)).fetchone()

            if proveedor and proveedor['proveedor_id']:
                proveedor_id = proveedor['proveedor_id']
                pedidos_por_proveedor.setdefault(proveedor_id, []).append({
                    'producto_id': producto_id,
                    'cantidad': cantidad,
                    'precio': _to_float_safe(proveedor['precio'], 0.0)
                })

        if not pedidos_por_proveedor:
            conn.close()
            flash('No se ha creado ningún pedido. Revisa que los productos tengan proveedor asociado.', 'warning')
            return redirect(url_for('compras.nuevo_pedido_tipo_a'))

        pedidos_creados = []
        fecha = datetime.now().strftime("%Y%m%d")
        cursor = conn.cursor()

        for proveedor_id, lineas in pedidos_por_proveedor.items():
            cursor.execute(
                "SELECT COUNT(*) as total FROM pedidos WHERE fecha_pedido = date('now') AND empresa_id = ?",
                (empresa_id,)
            )
            total_hoy = cursor.fetchone()['total'] + 1
            numero_pedido = f"PED-{fecha}-{total_hoy:03d}"

            cursor.execute("""
                INSERT INTO pedidos (
                    numero_pedido, proveedor_id, departamento_id, departamento_solicitante_id, fecha_entrega,
                    observaciones, creado_por, estado, empresa_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'borrador', ?)
            """, (
                numero_pedido,
                proveedor_id,
                departamento_id,
                departamento_id,
                fecha_entrega,
                observaciones,
                session.get('user_nombre', 'admin'),
                empresa_id
            ))

            pedido_id = cursor.lastrowid
            pedidos_creados.append(pedido_id)

            for linea in lineas:
                cursor.execute("""
                    INSERT INTO lineas_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario)
                    VALUES (?, ?, ?, ?)
                """, (pedido_id, linea['producto_id'], linea['cantidad'], linea['precio']))

        conn.commit()
        conn.close()

        if len(pedidos_creados) == 1:
            flash(' Pedido creado correctamente', 'success')
            return redirect(url_for('compras.ver_pedido', pedido_id=pedidos_creados[0]))

        flash(f' Se han creado {len(pedidos_creados)} pedidos (uno por proveedor)', 'success')
        return redirect(url_for('compras.lista_pedidos'))

    productos = conn.execute("""
        SELECT p.*,
               (SELECT proveedor_id
                FROM productos_proveedores
                WHERE producto_id = p.id
                  AND COALESCE(activo, 1) = 1
                ORDER BY COALESCE(es_principal, 0) DESC, id DESC
                LIMIT 1) as proveedor_principal_id
        FROM productos p
        WHERE COALESCE(p.activo, 1) = 1
          AND p.empresa_id = ?
        ORDER BY p.familia, p.nombre
    """, (empresa_id,)).fetchall()
    departamentos = _departamentos_visibles_compras(conn, empresa_id)

    conn.close()
    return render_template('compras/nuevo_pedido_tipo_a.html', productos=productos, departamentos=departamentos)

@compras_bp.route('/pedidos/nuevo-tipo-b', methods=['GET', 'POST'])
@login_required
def nuevo_pedido_tipo_b():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    bloqueo = _requiere_gestion_compras(conn, destino='compras.lista_pedidos')
    if bloqueo:
        return bloqueo

    if request.method == "POST":
        proveedor_id = request.form.get("proveedor_id")
        departamento_id = request.form.get("departamento_id")
        productos_seleccionados = request.form.getlist("producto_id[]")
        cantidades = request.form.getlist("cantidad[]")
        precios = request.form.getlist("precio[]")
        fecha_entrega = request.form.get("fecha_entrega", "")
        observaciones = request.form.get("observaciones", "")

        if not proveedor_id:
            conn.close()
            flash('Debes seleccionar proveedor.', 'warning')
            return redirect(url_for('compras.nuevo_pedido_tipo_b'))

        if not departamento_id:
            conn.close()
            flash('Debes seleccionar departamento.', 'warning')
            return redirect(url_for('compras.nuevo_pedido_tipo_b'))

        if not _departamento_form_permitido(departamento_id):
            conn.close()
            flash('No puedes crear pedidos para otro departamento.', 'warning')
            return redirect(url_for('compras.nuevo_pedido_tipo_b'))

        fecha = datetime.now().strftime("%Y%m%d")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as total FROM pedidos WHERE fecha_pedido = date('now') AND empresa_id = ?",
            (empresa_id,)
        )
        total_hoy = cursor.fetchone()['total'] + 1
        numero_pedido = f"PED-{fecha}-{total_hoy:03d}"

        cursor.execute("""
            INSERT INTO pedidos (
                numero_pedido, proveedor_id, departamento_id, departamento_solicitante_id, fecha_entrega,
                observaciones, creado_por, estado, empresa_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'borrador', ?)
        """, (
            numero_pedido,
            proveedor_id,
            departamento_id,
            departamento_id,
            fecha_entrega,
            observaciones,
            session.get('user_nombre', 'admin'),
            empresa_id
        ))

        pedido_id = cursor.lastrowid
        lineas_guardadas = 0

        for i in range(len(productos_seleccionados)):
            producto_id = productos_seleccionados[i]
            cantidad_raw = cantidades[i] if i < len(cantidades) else ''
            if not producto_id or not cantidad_raw:
                continue

            cantidad = _to_float_safe(cantidad_raw, 0.0)
            if cantidad <= 0:
                continue

            precio_linea = None
            if i < len(precios) and str(precios[i] or '').strip():
                precio_linea = _to_float_safe(precios[i], None)

            cursor.execute("""
                INSERT INTO lineas_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario)
                VALUES (?, ?, ?, ?)
            """, (pedido_id, producto_id, cantidad, precio_linea))
            lineas_guardadas += 1

        if lineas_guardadas == 0:
            conn.rollback()
            conn.close()
            flash('No se ha guardado ningún producto. Selecciona producto existente y cantidad.', 'warning')
            return redirect(url_for('compras.nuevo_pedido_tipo_b'))

        conn.commit()
        conn.close()

        flash(f' Pedido {numero_pedido} creado correctamente', 'success')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    proveedores = proveedores_select(conn, empresa_id, columnas="*")
    departamentos = _departamentos_visibles_compras(conn, empresa_id)

    columnas_productos = _columnas_tabla(cur, 'productos')
    columnas_pp = _columnas_tabla(cur, 'productos_proveedores')

    select_extra = []
    select_extra.append("p.codigo AS codigo" if 'codigo' in columnas_productos else "'' AS codigo")
    select_extra.append("p.codigo_interno AS codigo_interno" if 'codigo_interno' in columnas_productos else "'' AS codigo_interno")
    select_extra.append("p.unidad AS unidad" if 'unidad' in columnas_productos else "'' AS unidad")
    select_extra.append("p.familia AS familia" if 'familia' in columnas_productos else "'' AS familia")
    select_extra.append("p.precio AS precio_producto" if 'precio' in columnas_productos else "NULL AS precio_producto")
    select_extra.append("pp.codigo_proveedor AS codigo_proveedor" if 'codigo_proveedor' in columnas_pp else "'' AS codigo_proveedor")

    for col in ['precio', 'precio_compra', 'precio_ultimo', 'precio_vigente', 'precio_medio']:
        select_extra.append(f"pp.{col} AS {col}" if col in columnas_pp else f"NULL AS {col}")

    select_extra.append("pp.es_principal AS es_principal" if 'es_principal' in columnas_pp else "0 AS es_principal")

    productos = conn.execute(f"""
        SELECT p.id,
               p.nombre,
               pp.proveedor_id AS proveedor_id_producto,
               prov.nombre AS proveedor_nombre,
               {', '.join(select_extra)}
        FROM productos p
        JOIN productos_proveedores pp ON p.id = pp.producto_id
        JOIN proveedores prov ON pp.proveedor_id = prov.id
        WHERE COALESCE(prov.activo, 1) = 1
          AND prov.empresa_id = ?
          AND COALESCE(pp.activo, 1) = 1
        ORDER BY prov.nombre, p.nombre
    """, (empresa_id,)).fetchall()

    productos_json = []
    for p in productos:
        precio_actual = (
            p['precio_vigente'] if p['precio_vigente'] is not None else
            p['precio_ultimo'] if p['precio_ultimo'] is not None else
            p['precio_compra'] if p['precio_compra'] is not None else
            p['precio'] if p['precio'] is not None else
            p['precio_producto'] if p['precio_producto'] is not None else
            None
        )

        codigo_mostrar = p['codigo_proveedor'] or p['codigo'] or p['codigo_interno'] or ''
        etiqueta = f"{codigo_mostrar or 'SIN COD'} - {p['nombre'] or ''}"
        detalle = f"Proveedor: {p['proveedor_nombre'] or '-'}"
        if p['codigo_proveedor']:
            detalle += f" | Cód. proveedor: {p['codigo_proveedor']}"
        if p['codigo']:
            detalle += f" | Cód. ERP: {p['codigo']}"

        productos_json.append({
            'id': p['id'],
            'nombre': p['nombre'] or '',
            'codigo': p['codigo'] or '',
            'codigo_interno': p['codigo_interno'] or '',
            'codigo_proveedor': p['codigo_proveedor'] or '',
            'unidad': p['unidad'] or '',
            'familia': p['familia'] or '',
            'proveedor_id': p['proveedor_id_producto'],
            'proveedor_nombre': p['proveedor_nombre'] or '',
            'precio': precio_actual if precio_actual is not None else '',
            'es_principal': p['es_principal'] or 0,
            'etiqueta': etiqueta,
            'detalle': detalle,
            'texto_busqueda': " ".join([
                str(p['nombre'] or ''),
                str(p['codigo'] or ''),
                str(p['codigo_interno'] or ''),
                str(p['codigo_proveedor'] or ''),
                str(p['familia'] or ''),
            ])
        })

    conn.close()
    return render_template(
        'compras/nuevo_pedido_tipo_b.html',
        proveedores=proveedores,
        departamentos=departamentos,
        productos=productos,
        productos_json=productos_json
    )


# ============================================
# IMPRESIÓN RECEPCIÓN# ============================================
# IMPRESIÓN RECEPCIÓN
# ============================================

@compras_bp.route('/recepcion/<numero_pedido>/imprimir-conformidad')
@login_required
def imprimir_conformidad_recepcion(numero_pedido):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()

    scope_sql, scope_params = _scope_departamento('p', empresa_id)
    pedido = cur.execute(f"""
        SELECT p.*, prov.nombre AS proveedor_nombre
        FROM pedidos p
        LEFT JOIN proveedores prov ON prov.id = p.proveedor_id
        WHERE p.numero_pedido = ?
          AND {scope_sql}
    """, [numero_pedido, *scope_params]).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    recepcion = cur.execute("""
        SELECT *
        FROM recepciones
        WHERE pedido_numero = ? AND empresa_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (numero_pedido, empresa_id)).fetchone()

    lineas = cur.execute("""
        SELECT lr.*,
               lp.producto_id,
               pr.nombre,
               pr.codigo,
               pr.unidad
        FROM lineas_recepcion lr
        LEFT JOIN lineas_pedido lp ON lp.id = lr.linea_pedido_id
        LEFT JOIN productos pr ON pr.id = lp.producto_id
        WHERE lr.pedido_numero = ? AND lr.empresa_id = ?
        ORDER BY lr.id
    """, (numero_pedido, empresa_id)).fetchall()

    conn.close()
    return render_template(
        'compras/hoja_conformidad.html',
        pedido=pedido,
        recepcion=recepcion,
        lineas=lineas
    )


@compras_bp.route('/recepcion/<numero_pedido>/imprimir-incidencias')
@login_required
def imprimir_incidencias_recepcion(numero_pedido):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()

    scope_sql, scope_params = _scope_departamento('p', empresa_id)
    pedido = cur.execute(f"""
        SELECT p.*, prov.nombre AS proveedor_nombre
        FROM pedidos p
        LEFT JOIN proveedores prov ON prov.id = p.proveedor_id
        WHERE p.numero_pedido = ?
          AND {scope_sql}
    """, [numero_pedido, *scope_params]).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    recepcion = cur.execute("""
        SELECT *
        FROM recepciones
        WHERE pedido_numero = ? AND empresa_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (numero_pedido, empresa_id)).fetchone()

    incidencias = cur.execute("""
        SELECT i.*,
               pr.nombre AS producto_nombre,
               pr.codigo AS producto_codigo
        FROM incidencias i
        LEFT JOIN productos pr ON pr.id = i.producto_id
        WHERE i.empresa_id = ?
          AND (
                i.referencia_id = ?
                OR i.documento_origen = ?
              )
        ORDER BY i.id DESC
    """, (empresa_id, numero_pedido, numero_pedido)).fetchall()

    conn.close()
    return render_template(
        'compras/hoja_incidencia_recepcion.html',
        pedido=pedido,
        recepcion=recepcion,
        incidencias=incidencias
    )
@compras_bp.route('/pedidos/<int:pedido_id>/enviar', methods=['POST'])
@login_required
def enviar_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    scope_sql, scope_params = _scope_departamento('pedidos', empresa_id)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    pedido = conn.execute(
        f"SELECT * FROM pedidos WHERE id = ? AND {scope_sql}",
        [pedido_id, *scope_params],
    ).fetchone()
    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))
    bloqueo = _requiere_gestion_compras(conn, pedido['departamento_id'], 'compras.lista_pedidos')
    if bloqueo:
        return bloqueo
    if pedido['estado'] == 'enviado':
        conn.close()
        flash('El pedido ya estaba marcado como enviado. Esta accion no envia correos.', 'info')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))
    if pedido['estado'] != 'autorizado':
        conn.close()
        flash('El pedido debe estar autorizado antes de enviarse al proveedor.', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    ok_autorizacion, mensaje = _pedido_preparado_para_autorizacion(cur, pedido_id)
    if not ok_autorizacion:
        conn.close()
        flash(mensaje, 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    cursor = cur.execute(f"""
        UPDATE pedidos
        SET estado = 'enviado',
            fecha_envio = datetime('now')
        WHERE id = ?
          AND {scope_sql}
          AND estado = 'autorizado'
    """, [pedido_id, *scope_params])
    if cursor.rowcount:
        _registrar_auditoria_compra(
            cur,
            empresa_id,
            'pedido_enviado',
            referencia_tipo='pedido',
            referencia_id=pedido_id,
            detalle='Pedido marcado como enviado a proveedor',
            departamento_id=pedido['departamento_id'],
            proveedor_id=pedido['proveedor_id'],
            estado_anterior=pedido['estado'],
            estado_nuevo='enviado',
        )

    conn.commit()
    conn.close()

    if cursor.rowcount == 0:
        flash('El pedido ha cambiado o ya no es accesible. Recarga antes de continuar.', 'warning')
        return redirect(url_for('compras.lista_pedidos'))

    flash('Pedido marcado como enviado. Esta accion no envia correos al proveedor.', 'success')
    return redirect(url_for('compras.lista_pedidos'))


@compras_bp.route('/recepcion-directa', methods=['GET', 'POST'])
@login_required
def recepcion_directa():
    conn = get_db(); empresa_id = session.get('user_empresa_id', 1); usuario = (session.get('user_nombre') or 'admin').strip()
    ver_precios = _usuario_puede_ver_precios_compras()
    compra_rapida = request.args.get('rapida') == '1' or request.form.get('compra_rapida') == '1'
    modo_tablet = request.args.get('tablet') == '1' or request.form.get('modo_tablet') == '1'
    cur = conn.cursor(); _asegurar_estructura_compras(cur); conn.commit()
    bloqueo = _requiere_recepcion_compras(conn, destino='compras.validar_recepciones')
    if bloqueo:
        return bloqueo
    proveedores=[dict(p) for p in proveedores_select(conn, empresa_id)]
    almacenes=[dict(a) for a in _almacenes_visibles_compras(conn, empresa_id)]
    almacenes_ids={int(a['id']) for a in almacenes}
    departamentos=[dict(d) for d in _departamentos_visibles_compras(conn, empresa_id)]
    productos=[dict(p) for p in conn.execute("""
        SELECT id, nombre, codigo, precio
        FROM productos
        WHERE COALESCE(activo, 1) = 1
          AND empresa_id = ?
          AND COALESCE(visible_en_compras, 1) = 1
          AND COALESCE(permite_compra_directa, 1) = 1
        ORDER BY nombre
    """,(empresa_id,)).fetchall()]
    if not ver_precios:
        for p in productos:
            p['precio'] = ''
    if request.method == 'POST':
        accion=(request.form.get('accion') or 'guardar').strip().lower(); proveedor_id=_to_int_or_none(request.form.get('proveedor_id')); departamento_id=_to_int_or_none(request.form.get('departamento_id'))
        numero_albaran=(request.form.get('numero_albaran') or '').strip(); observaciones=(request.form.get('observaciones') or '').strip(); documento=request.files.get('documento_compra')
        hay_documento=bool(documento and documento.filename)
        if accion == 'leer':
            lineas_ocr=[]; texto_ocr=''
            if not hay_documento: flash('Selecciona un albarán, foto o PDF para leer.', 'warning')
            else:
                ruta_tmp=None
                try:
                    if documento is None or not documento.filename:
                        flash('Selecciona un albarán, foto o PDF para leer.', 'warning')
                        raise ValueError('Documento no seleccionado')
                    nombre_original=secure_filename(str(documento.filename or 'documento')); extension=os.path.splitext(nombre_original)[1].lower(); nombre_tmp=f'tmp_ocr_{uuid.uuid4().hex}{extension}'
                    tmp_dir=os.path.join(current_app.instance_path,'uploads','compras','tmp_ocr'); os.makedirs(tmp_dir, exist_ok=True); ruta_tmp=os.path.join(tmp_dir,nombre_tmp); documento.save(ruta_tmp)
                    texto_ocr=extraer_texto_documento_compra(ruta_tmp)
                    numero_documento_detectado = detectar_numero_albaran_desde_texto(texto_ocr)
                    if numero_documento_detectado and not numero_albaran:
                        numero_albaran = numero_documento_detectado
                    numero_pedido_detectado = detectar_numero_pedido_desde_texto(texto_ocr)
                    if numero_pedido_detectado:
                        scope_sql, scope_params = _scope_departamento('pedidos', empresa_id)
                        pedido_detectado = conn.execute(f"""
                            SELECT id, numero_pedido
                            FROM pedidos
                            WHERE numero_pedido = ?
                              AND {scope_sql}
                            LIMIT 1
                        """, [numero_pedido_detectado, *scope_params]).fetchone()
                        if pedido_detectado:
                            recepcion_existente = conn.execute("""
                                SELECT id
                                FROM recepciones
                                WHERE pedido_id = ?
                                  AND empresa_id = ?
                                  AND COALESCE(tipo_recepcion, 'pedido') = 'pedido'
                                ORDER BY id DESC
                                LIMIT 1
                            """, (pedido_detectado['id'], empresa_id)).fetchone()
                            conn.close()
                            if recepcion_existente:
                                flash(f'OCR detectó el pedido {numero_pedido_detectado}. Ya está recepcionado; abre edición para corregir sin duplicar stock.', 'warning')
                                return redirect(url_for('compras.editar_recepcion', recepcion_id=recepcion_existente['id']))
                            flash(f'OCR detectó el pedido {numero_pedido_detectado}. Recepciona desde el pedido para no crear entrada directa.', 'info')
                            return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido_detectado))
                    centro_recepcion_id = None
                    if departamento_id:
                        centro_row = conn.execute("""
                            SELECT centro_id
                            FROM departamentos
                            WHERE id = ? AND (empresa_id = ? OR empresa_id IS NULL)
                        """, (departamento_id, empresa_id)).fetchone()
                        centro_recepcion_id = centro_row['centro_id'] if centro_row else None
                    for idx,linea in enumerate(detectar_lineas_documento_compra(texto_ocr), start=1):
                        producto_id=buscar_producto_por_texto(conn, linea.get('descripcion') or '', empresa_id, referencia=linea.get('referencia') or '', proveedor_id=proveedor_id, centro_id=centro_recepcion_id)
                        lineas_ocr.append({'idx':idx,'descripcion':linea.get('descripcion') or '','producto_id':producto_id or '','cantidad':linea.get('cantidad') or '','precio':linea.get('precio') or '','importe':linea.get('importe') or '','almacen_id':'','lote':'','fecha_caducidad':'','temperatura':'','incidencia_texto':''})
                    flash(f'Se han leído {len(lineas_ocr)} líneas. Revisa antes de guardar.', 'success')
                except Exception as e: flash(f'Error leyendo documento: {str(e)}', 'danger')
                finally:
                    try:
                        if ruta_tmp and os.path.exists(ruta_tmp): os.remove(ruta_tmp)
                    except Exception: pass
            form_cache={'proveedor_id':proveedor_id or '', 'departamento_id':departamento_id or '', 'numero_albaran':numero_albaran, 'observaciones':observaciones}
            conn.close(); return render_template('compras/recepcion_directa.html', proveedores=proveedores, productos=productos, almacenes=almacenes, departamentos=departamentos, lineas_ocr=lineas_ocr, texto_ocr=texto_ocr, form_cache=form_cache, ver_precios=ver_precios, compra_rapida=compra_rapida, modo_tablet=modo_tablet)
        if not proveedor_id or not departamento_id:
            conn.close(); flash('Debes seleccionar proveedor y departamento.', 'warning'); return redirect(url_for('compras.recepcion_directa'))
        proveedor = conn.execute("""
            SELECT id
            FROM proveedores
            WHERE id = ?
              AND empresa_id = ?
              AND COALESCE(activo, 1) = 1
        """, (proveedor_id, empresa_id)).fetchone()
        if not proveedor:
            conn.close(); flash('Proveedor no encontrado o inactivo.', 'danger'); return redirect(url_for('compras.recepcion_directa'))
        if not _departamento_recepcion_form_permitido(departamento_id):
            conn.close(); flash('No puedes recepcionar material para otro departamento.', 'warning'); return redirect(url_for('compras.recepcion_directa'))
        almacenes_departamento=[dict(a) for a in _almacenes_visibles_compras(conn, empresa_id, departamento_id)]
        almacenes_ids={int(a['id']) for a in almacenes_departamento}
        if not almacenes_ids:
            conn.close(); flash('Este departamento no tiene almacenes activos para recepcionar mercancia.', 'warning'); return redirect(url_for('compras.recepcion_directa'))
        indices=request.form.getlist('linea_idx[]')
        if not indices:
            conn.close(); flash('No hay líneas para guardar. Añade productos o lee un albarán.', 'warning'); return redirect(url_for('compras.recepcion_directa'))
        try:
            cur=conn.cursor(); _asegurar_estructura_compras(cur)
            tipo_recepcion = 'compra_rapida' if (request.form.get('compra_rapida') == '1' or request.args.get('rapida') == '1') else 'directa'
            cur.execute("""INSERT INTO recepciones (pedido_id, proveedor_id, departamento_id, departamento_receptor_id, fecha_recepcion, numero_albaran, observaciones, recibido_por, empresa_id, tipo_recepcion, validada_operativamente, validada_economicamente, stock_aplicado) VALUES (NULL, ?, ?, ?, date('now'), ?, ?, ?, ?, ?, 1, 0, 0)""",(proveedor_id,departamento_id,departamento_id,numero_albaran,observaciones,usuario,empresa_id,tipo_recepcion))
            recepcion_id=cur.lastrowid
            documento_id=guardar_documento_compra_en_cursor(cur, documento, proveedor_id=proveedor_id, pedido_id=None, recepcion_id=recepcion_id, tipo_documento='ticket' if tipo_recepcion == 'compra_rapida' else 'albaran', origen_subida='ticket' if tipo_recepcion == 'compra_rapida' else ('foto' if hay_documento else 'manual'))
            if documento_id: cur.execute('UPDATE recepciones SET documento_adjunto_id = ? WHERE id = ?', (documento_id,recepcion_id))
            lineas_guardadas=0; incidencias_guardadas=0
            for idx in indices:
                producto_id=_to_int_or_none(request.form.get(f'producto_id_{idx}')); cantidad=_to_float_safe(request.form.get(f'cantidad_{idx}')); precio=_to_float_safe(request.form.get(f'precio_{idx}')); importe=_to_float_safe(request.form.get(f'importe_{idx}')); almacen_id=_to_int_or_none(request.form.get(f'almacen_id_{idx}'))
                num_envases=_to_int_or_none(request.form.get(f'envases_{idx}')) or 1
                if num_envases < 1: num_envases=1
                estado=(request.form.get(f'estado_{idx}') or 'ok').strip(); incidencia_texto=(request.form.get(f'incidencia_texto_{idx}') or '').strip(); descripcion=(request.form.get(f'descripcion_{idx}') or '').strip(); lote=(request.form.get(f'lote_{idx}') or '').strip(); caducidad=(request.form.get(f'caducidad_{idx}') or '').strip() or None
                temp_raw=(request.form.get(f'temperatura_{idx}') or '').strip(); temperatura=_to_float_safe(temp_raw, 0.0) if temp_raw else None; tiene_incidencia=estado!='ok'
                if not producto_id: continue
                producto = cur.execute("""
                    SELECT id
                    FROM productos
                    WHERE id = ?
                      AND empresa_id = ?
                      AND COALESCE(activo, 1) = 1
                      AND COALESCE(visible_en_compras, 1) = 1
                      AND COALESCE(permite_compra_directa, 1) = 1
                """, (producto_id, empresa_id)).fetchone()
                if not producto:
                    continue
                if cantidad <= 0 and not tiene_incidencia: continue
                if cantidad > 0 and not almacen_id:
                    conn.rollback(); flash('Cada línea recibida debe tener almacén destino.', 'warning'); return redirect(url_for('compras.recepcion_directa'))
                if almacen_id and almacen_id not in almacenes_ids:
                    conn.rollback(); flash('No puedes recepcionar material en un almacen de otro departamento.', 'warning'); return redirect(url_for('compras.recepcion_directa'))
                if precio <= 0 and importe > 0 and cantidad > 0: precio=round(importe/cantidad,6)
                incidencia_id=None; aceptado=1; motivo_rechazo=None
                if tiene_incidencia:
                    tipo_map={'falta_unidades':'Falta de unidades','falta_peso':'Falta de peso','rotura':'Rotura','mal_estado':'Mal estado','temperatura_incorrecta':'Temperatura incorrecta'}; incidencia_tipo=tipo_map.get(estado,'Incidencia en recepción directa')
                    if not incidencia_texto: incidencia_texto=f'{incidencia_tipo} en recepción directa. Producto: {descripcion or producto_id}.'
                    motivo_rechazo=incidencia_texto
                    if estado in ('rotura','mal_estado','temperatura_incorrecta'): aceptado=0
                    incidencia_id=_crear_incidencia_recepcion(cur, empresa_id, recepcion_id, usuario, incidencia_tipo, incidencia_texto, gravedad='alta' if aceptado == 0 else 'media'); incidencias_guardadas += 1
                importe_total=round(cantidad*precio,2) if precio else None
                cur.execute("""INSERT INTO lineas_recepcion (recepcion_id, producto_id, cantidad_recibida, lote, fecha_caducidad, temperatura, almacen_id, precio_unitario, importe_total, aceptado, motivo_rechazo, incidencia_id, empresa_id, num_envases) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",(recepcion_id,producto_id,cantidad,lote or None,caducidad,temperatura,almacen_id,precio if precio else None,importe_total,aceptado,motivo_rechazo,incidencia_id,empresa_id,num_envases))
                if descripcion:
                    _guardar_alias_producto_compra(cur, empresa_id, proveedor_id, producto_id, descripcion)
                lineas_guardadas += 1
            if lineas_guardadas == 0:
                conn.rollback(); flash('No se ha guardado ninguna línea válida.', 'warning'); return redirect(url_for('compras.recepcion_directa'))
            aplicadas=_aplicar_stock_recepcion(cur, recepcion_id, empresa_id, usuario)
            _registrar_auditoria_compra(cur, empresa_id, 'compra_rapida' if tipo_recepcion == 'compra_rapida' else 'recepcion_directa', referencia_tipo='recepcion', referencia_id=recepcion_id, detalle=f'{lineas_guardadas} lineas, {incidencias_guardadas} incidencias, stock {aplicadas}', departamento_id=departamento_id, proveedor_id=proveedor_id, estado_nuevo='recepcion_operativa', usuario=usuario)
            conn.commit(); flash(f'Recepción directa guardada: {lineas_guardadas} línea(s), {incidencias_guardadas} incidencia(s), stock aplicado en {aplicadas} línea(s). Validación económica pendiente.', 'success')
            if request.form.get('imprimir_etiquetas') == '1':
                return redirect(url_for('compras.etiquetas_recepcion', recepcion_id=recepcion_id))
            return redirect(url_for('compras.validar_recepciones'))
        except Exception as e:
            conn.rollback(); flash(f'Error guardando recepción directa: {str(e)}', 'danger'); return redirect(url_for('compras.recepcion_directa'))
        finally: conn.close()
    conn.close(); return render_template('compras/recepcion_directa.html', proveedores=proveedores, productos=productos, almacenes=almacenes, departamentos=departamentos, lineas_ocr=[], texto_ocr='', form_cache=None, ver_precios=ver_precios, compra_rapida=compra_rapida, modo_tablet=modo_tablet)


@compras_bp.route('/recepciones/<int:recepcion_id>/conformidad')
@login_required
def hoja_conformidad_recepcion_id(recepcion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    scope_sql, scope_params, centro_activo_id = _scope_recepciones_centro(conn, 'r', empresa_id)
    recepcion = conn.execute(f"""
        SELECT r.*, pr.nombre AS proveedor_nombre, pr.nif,
               COALESCE(NULLIF(pr.email_pedidos, ''), pr.email) AS email,
               COALESCE(NULLIF(pr.telefono_pedidos, ''), pr.telefono) AS telefono,
               p.numero_pedido, p.fecha_pedido
        FROM recepciones r
        LEFT JOIN proveedores pr ON r.proveedor_id = pr.id
        LEFT JOIN pedidos p ON r.pedido_id = p.id
        WHERE r.id = ?
          AND {scope_sql}
    """, [recepcion_id, *scope_params]).fetchone()

    if not recepcion:
        conn.close()
        flash('Recepción no encontrada.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    lineas = conn.execute("""
        SELECT lr.*, prod.nombre AS producto_nombre, prod.codigo, prod.unidad
        FROM lineas_recepcion lr
        JOIN productos prod ON lr.producto_id = prod.id
        WHERE lr.recepcion_id = ?
          AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
        ORDER BY lr.id
    """, (recepcion_id, empresa_id)).fetchall()

    conn.close()
    return render_template(
        'compras/hoja_conformidad.html',
        recepcion=recepcion,
        lineas=lineas,
        now=datetime.now()
    )


@compras_bp.route('/recepciones/<int:recepcion_id>/etiquetas')
@login_required
def etiquetas_recepcion(recepcion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    formato_codigo = request.args.get('formato')
    device_uid = request.cookies.get('el_raco_device_uid')
    formatos_etiqueta = get_label_configs(conn, empresa_id, solo_activas=True)
    dispositivo_impresion = get_current_print_device(conn, empresa_id, device_uid)
    config_etiqueta = get_label_config(conn, empresa_id, formato_codigo, device_uid)
    conn.commit()

    scope_sql, scope_params, centro_activo_id = _scope_recepciones_centro(conn, 'r', empresa_id)
    recepcion = conn.execute(f"""
        SELECT r.*, pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre,
               p.numero_pedido
        FROM recepciones r
        LEFT JOIN proveedores pr ON pr.id = r.proveedor_id
        LEFT JOIN departamentos d ON d.id = r.departamento_id
        LEFT JOIN pedidos p ON p.id = r.pedido_id
        WHERE r.id = ?
          AND {scope_sql}
    """, [recepcion_id, *scope_params]).fetchone()

    if not recepcion:
        conn.close()
        flash('Recepcion no encontrada.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    lineas = conn.execute("""
        SELECT lr.*,
               prod.nombre AS producto_nombre, prod.codigo, prod.unidad, prod.familia,
               prod.tipo_conservacion, prod.requiere_desinfeccion,
               a.nombre AS almacen_nombre,
               l.id AS lote_real_id, l.codigo_lote, l.origen_lote, l.lote_proveedor,
               l.estado AS estado_lote,
               (SELECT COUNT(*) FROM registros_desinfeccion rd WHERE rd.lote_id = l.id) AS num_desinfecciones
        FROM lineas_recepcion lr
        JOIN productos prod ON prod.id = lr.producto_id
        LEFT JOIN almacenes a ON a.id = lr.almacen_id
        LEFT JOIN lotes l ON l.id = lr.lote_id
        WHERE lr.recepcion_id = ?
          AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
          AND COALESCE(lr.cantidad_recibida, 0) > 0
          AND COALESCE(lr.aceptado, 1) = 1
        ORDER BY lr.id
    """, (recepcion_id, empresa_id)).fetchall()
    conn.close()

    etiquetas = []
    for linea in lineas:
        num_envases = _to_int_or_none(linea['num_envases'] if 'num_envases' in linea.keys() else None) or 1
        if num_envases < 1:
            num_envases = 1
        cantidad_total = _to_float_safe(linea['cantidad_recibida'], 0.0)
        cantidad_envase = cantidad_total / num_envases if num_envases else cantidad_total
        codigo_lote = (
            linea['codigo_lote']
            or linea['lote']
            or f"REC-{recepcion_id}-{linea['producto_id']}-{linea['id']}"
        )
        for copia in range(1, num_envases + 1):
            etiquetas.append({
                'linea': linea,
                'codigo_lote': codigo_lote,
                'copia': copia,
                'total_copias': num_envases,
                'cantidad_envase': cantidad_envase,
                'cantidad_total': cantidad_total,
            })

    return render_template(
        'compras/etiquetas_recepcion.html',
        recepcion=recepcion,
        etiquetas=etiquetas,
        now=datetime.now(),
        formatos_etiqueta=formatos_etiqueta,
        config_etiqueta=config_etiqueta,
        dispositivo_impresion=dispositivo_impresion,
        label_css=build_label_css(config_etiqueta),
        zpl_contenido=zpl_etiquetas_recepcion(etiquetas, recepcion, config_etiqueta),
    )


@compras_bp.route('/recepciones/<int:recepcion_id>/desinfeccion', methods=['GET', 'POST'])
@login_required
def desinfeccion_recepcion(recepcion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    usuario = (session.get('user_nombre') or 'admin').strip()
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    scope_sql, scope_params, centro_id = _scope_recepciones_centro(conn, 'r', empresa_id)
    recepcion = conn.execute(f"""
        SELECT r.*, pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre,
               p.numero_pedido
        FROM recepciones r
        LEFT JOIN proveedores pr ON pr.id = r.proveedor_id
        LEFT JOIN departamentos d ON d.id = r.departamento_id
        LEFT JOIN pedidos p ON p.id = r.pedido_id
        WHERE r.id = ?
          AND {scope_sql}
    """, [recepcion_id, *scope_params]).fetchone()

    if not recepcion:
        conn.close()
        flash('Recepcion no encontrada.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    lineas = conn.execute("""
        SELECT lr.id AS linea_id, lr.cantidad_recibida, lr.lote AS lote_proveedor,
               prod.nombre AS producto_nombre, prod.unidad, prod.familia,
               prod.requiere_desinfeccion,
               l.id AS lote_id, l.codigo_lote, l.cantidad_actual, l.fecha_caducidad,
               a.nombre AS almacen_nombre,
               (SELECT COUNT(*) FROM registros_desinfeccion rd WHERE rd.lote_id = l.id) AS num_desinfecciones
        FROM lineas_recepcion lr
        JOIN productos prod ON prod.id = lr.producto_id
        LEFT JOIN lotes l ON l.id = lr.lote_id
        LEFT JOIN almacenes a ON a.id = lr.almacen_id
        WHERE lr.recepcion_id = ?
          AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
          AND COALESCE(lr.cantidad_recibida, 0) > 0
          AND COALESCE(lr.aceptado, 1) = 1
          AND l.id IS NOT NULL
          AND (
                COALESCE(prod.requiere_desinfeccion, 0) = 1
                OR LOWER(COALESCE(prod.familia, '') || ' ' || COALESCE(prod.nombre, '')) LIKE '%fruta%'
                OR LOWER(COALESCE(prod.familia, '') || ' ' || COALESCE(prod.nombre, '')) LIKE '%verdura%'
                OR LOWER(COALESCE(prod.familia, '') || ' ' || COALESCE(prod.nombre, '')) LIKE '%vegetal%'
                OR LOWER(COALESCE(prod.familia, '') || ' ' || COALESCE(prod.nombre, '')) LIKE '%lechuga%'
                OR LOWER(COALESCE(prod.familia, '') || ' ' || COALESCE(prod.nombre, '')) LIKE '%tomate%'
          )
        ORDER BY prod.nombre
    """, (recepcion_id, empresa_id)).fetchall()

    if request.method == 'POST':
        bloqueo = _requiere_recepcion_compras(conn, recepcion['departamento_id'], destino='compras.validar_recepciones')
        if bloqueo:
            return bloqueo
        if not centro_id:
            conn.close()
            flash('Selecciona un centro antes de registrar desinfecciones.', 'warning')
            return redirect(url_for('rrhh.centro_empresa_general'))
        lote_ids = [int(v) for v in request.form.getlist('lote_id') if str(v).isdigit()]
        producto_usado = request.form.get('producto_usado', '').strip()
        dosis = request.form.get('dosis', '').strip()
        tiempo_contacto = request.form.get('tiempo_contacto', '').strip()
        resultado = request.form.get('resultado', 'apto')
        observaciones = request.form.get('observaciones', '').strip()
        aclarado = 1 if request.form.get('aclarado') else 0

        lotes_validos = {
            int(row['lote_id'])
            for row in lineas
            if row['lote_id'] is not None
        }
        lote_ids = [lote_id for lote_id in lote_ids if lote_id in lotes_validos]

        if not lote_ids:
            flash('Selecciona al menos un lote de esta recepcion.', 'warning')
        else:
            documento = recepcion['numero_pedido'] or recepcion['numero_albaran'] or f"Recepcion {recepcion_id}"
            for lote_id in lote_ids:
                conn.execute("""
                    INSERT INTO registros_desinfeccion (
                        lote_id, producto_usado, dosis, tiempo_contacto, aclarado,
                        resultado, responsable, observaciones, empresa_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    lote_id, producto_usado, dosis, tiempo_contacto, aclarado,
                    resultado, usuario, observaciones, empresa_id
                ))
                conn.execute("""
                    INSERT INTO movimientos_lotes (
                        lote_id, tipo, cantidad, destino_tipo, usuario, observaciones, documento, empresa_id
                    )
                    VALUES (?, 'desinfeccion', 0, 'camara_segura', ?, ?, ?, ?)
                """, (lote_id, usuario, observaciones or f'Resultado {resultado}', documento, empresa_id))
                conn.execute("""
                    UPDATE lotes
                    SET notas_trazabilidad = TRIM(COALESCE(notas_trazabilidad, '') || ' | Desinfeccion recepcion: ' || ?)
                    WHERE id = ? AND empresa_id = ?
                """, (resultado, lote_id, empresa_id))

            conn.commit()
            conn.close()
            flash(f'Desinfeccion registrada en {len(lote_ids)} lote(s).', 'success')
            if request.form.get('imprimir_etiquetas') == '1':
                return redirect(url_for('compras.etiquetas_recepcion', recepcion_id=recepcion_id))
            return redirect(url_for('compras.validar_recepciones'))

    conn.close()
    return render_template(
        'compras/desinfeccion_recepcion.html',
        recepcion=recepcion,
        lineas=lineas,
    )


@compras_bp.route('/recepciones/<int:recepcion_id>/incidencias')
@login_required
def hoja_incidencias_recepcion_id(recepcion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    scope_sql, scope_params = _scope_departamento('r', empresa_id)
    recepcion = conn.execute(f"""
        SELECT r.*, pr.nombre AS proveedor_nombre, p.numero_pedido
        FROM recepciones r
        LEFT JOIN proveedores pr ON r.proveedor_id = pr.id
        LEFT JOIN pedidos p ON r.pedido_id = p.id
        WHERE r.id = ?
          AND {scope_sql}
    """, [recepcion_id, *scope_params]).fetchone()

    if not recepcion:
        conn.close()
        flash('Recepción no encontrada.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    pedido = {
        'id': recepcion['pedido_id'],
        'numero_pedido': recepcion['numero_pedido'] or 'Recepción directa',
        'proveedor_nombre': recepcion['proveedor_nombre'] or '-',
    }

    incidencias = conn.execute("""
        SELECT i.*, lr.producto_id, prod.nombre AS producto_nombre,
               r.fecha_recepcion, r.numero_albaran
        FROM incidencias i
        LEFT JOIN lineas_recepcion lr ON lr.incidencia_id = i.id
        LEFT JOIN recepciones r ON lr.recepcion_id = r.id
        LEFT JOIN productos prod ON lr.producto_id = prod.id
        WHERE r.id = ? AND i.empresa_id = ?
        ORDER BY i.id DESC
    """, (recepcion_id, empresa_id)).fetchall()

    conn.close()
    return render_template(
        'compras/hoja_incidencias_recepcion.html',
        pedido=pedido,
        incidencias=incidencias,
        now=datetime.now()
    )
@compras_bp.route('/recepciones/<int:recepcion_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_recepcion(recepcion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    usuario = (session.get('user_nombre') or 'admin').strip()
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    scope_sql, scope_params = _scope_departamento('r', empresa_id)
    recepcion = conn.execute(f"""
        SELECT r.*, pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre,
               p.numero_pedido, p.id AS pedido_id_real
        FROM recepciones r
        LEFT JOIN proveedores pr ON pr.id = r.proveedor_id
        LEFT JOIN departamentos d ON d.id = r.departamento_id
        LEFT JOIN pedidos p ON p.id = r.pedido_id
        WHERE r.id = ?
          AND {scope_sql}
    """, [recepcion_id, *scope_params]).fetchone()

    if not recepcion:
        conn.close()
        flash('Recepción no encontrada.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    almacenes = _almacenes_visibles_compras(conn, empresa_id, recepcion['departamento_id'])
    almacenes_ids = {int(a['id']) for a in almacenes}

    bloqueo = _requiere_recepcion_compras(conn, recepcion['departamento_id'], 'compras.validar_recepciones')
    if bloqueo:
        return bloqueo

    if request.method == 'POST':
        try:
            numero_albaran = (request.form.get('numero_albaran') or '').strip()
            observaciones = (request.form.get('observaciones') or '').strip()
            fecha_recepcion = (request.form.get('fecha_recepcion') or recepcion['fecha_recepcion'] or datetime.now().strftime('%Y-%m-%d')).strip()

            lineas = conn.execute("""
                SELECT *
                FROM lineas_recepcion
                WHERE recepcion_id = ?
                  AND (empresa_id = ? OR empresa_id IS NULL)
                ORDER BY id
            """, (recepcion_id, empresa_id)).fetchall()

            documento = numero_albaran or recepcion['numero_pedido'] or f"RECEP-{recepcion_id}"

            for linea in lineas:
                linea_id = linea['id']
                cantidad_anterior = _to_float_safe(linea['cantidad_recibida'], 0.0)
                almacen_anterior = linea['almacen_id']
                aceptado_anterior = int(linea['aceptado'] if linea['aceptado'] is not None else 1)

                cantidad_nueva = _to_float_safe(request.form.get(f'cantidad_{linea_id}'), 0.0)
                almacen_nuevo = _to_int_or_none(request.form.get(f'almacen_{linea_id}'))
                lote_nuevo = (request.form.get(f'lote_{linea_id}') or '').strip()
                caducidad_nueva = (request.form.get(f'caducidad_{linea_id}') or '').strip() or None
                temperatura_raw = (request.form.get(f'temperatura_{linea_id}') or '').strip()
                temperatura_nueva = _to_float_safe(temperatura_raw, 0.0) if temperatura_raw else None
                estado = (request.form.get(f'estado_{linea_id}') or 'ok').strip()
                incidencia_texto = (request.form.get(f'incidencia_texto_{linea_id}') or '').strip()

                if cantidad_nueva < 0:
                    cantidad_nueva = 0.0

                if cantidad_nueva > 0 and not almacen_nuevo:
                    conn.rollback()
                    flash('Toda línea con cantidad recibida debe tener almacén destino.', 'warning')
                    return redirect(url_for('compras.editar_recepcion', recepcion_id=recepcion_id))

                if almacen_nuevo and almacen_nuevo not in almacenes_ids:
                    conn.rollback()
                    flash('No puedes mover material a un almacen de otro departamento.', 'warning')
                    return redirect(url_for('compras.editar_recepcion', recepcion_id=recepcion_id))

                aceptado_nuevo = 0 if estado in ('rotura', 'mal_estado', 'temperatura_incorrecta', 'rechazado') else 1
                motivo_rechazo = incidencia_texto if aceptado_nuevo == 0 or estado != 'ok' else None

                cantidad_stock_anterior = cantidad_anterior if aceptado_anterior == 1 else 0.0
                cantidad_stock_nueva = cantidad_nueva if aceptado_nuevo == 1 else 0.0

                if almacen_anterior and almacen_nuevo and int(almacen_anterior) != int(almacen_nuevo):
                    if cantidad_stock_anterior:
                        _ajustar_stock_almacen(cur, linea['producto_id'], almacen_anterior, -cantidad_stock_anterior, empresa_id, usuario, documento, 'Ajuste edición recepción: cambio de almacén')
                    if cantidad_stock_nueva:
                        _ajustar_stock_almacen(cur, linea['producto_id'], almacen_nuevo, cantidad_stock_nueva, empresa_id, usuario, documento, 'Ajuste edición recepción: cambio de almacén')
                else:
                    almacen_para_ajuste = almacen_nuevo or almacen_anterior
                    diferencia = cantidad_stock_nueva - cantidad_stock_anterior
                    if diferencia:
                        _ajustar_stock_almacen(cur, linea['producto_id'], almacen_para_ajuste, diferencia, empresa_id, usuario, documento, 'Ajuste edición recepción: corrección de cantidad/estado')

                incidencia_id = linea['incidencia_id']
                if estado != 'ok' and not incidencia_id:
                    tipo_map = {
                        'falta_unidades': 'Falta de unidades',
                        'falta_peso': 'Falta de peso',
                        'rotura': 'Rotura',
                        'mal_estado': 'Mal estado',
                        'temperatura_incorrecta': 'Temperatura incorrecta',
                        'rechazado': 'Producto rechazado'
                    }
                    incidencia_tipo = tipo_map.get(estado, 'Incidencia en recepción')
                    if not incidencia_texto:
                        incidencia_texto = f'{incidencia_tipo} corregida en edición de recepción {recepcion_id}.'
                    incidencia_id = _crear_incidencia_recepcion(cur, empresa_id, recepcion_id, usuario, incidencia_tipo, incidencia_texto, gravedad='alta' if aceptado_nuevo == 0 else 'media')

                cur.execute("""
                    UPDATE lineas_recepcion
                    SET cantidad_recibida = ?,
                        almacen_id = ?,
                        lote = ?,
                        fecha_caducidad = ?,
                        temperatura = ?,
                        aceptado = ?,
                        motivo_rechazo = ?,
                        incidencia_id = ?
                    WHERE id = ?
                """, (
                    cantidad_nueva,
                    almacen_nuevo,
                    lote_nuevo or None,
                    caducidad_nueva,
                    temperatura_nueva,
                    aceptado_nuevo,
                    motivo_rechazo,
                    incidencia_id,
                    linea_id
                ))

                _ajustar_lote_recepcion_linea(cur, recepcion, linea, cantidad_stock_nueva, almacen_nuevo, lote_nuevo, caducidad_nueva, temperatura_nueva)

            cur.execute("""
                UPDATE recepciones
                SET numero_albaran = ?,
                    observaciones = ?,
                    fecha_recepcion = ?,
                    validada_economicamente = 0
                WHERE id = ? AND empresa_id = ?
            """, (numero_albaran, observaciones, fecha_recepcion, recepcion_id, empresa_id))

            if recepcion['pedido_id']:
                _recalcular_cantidad_recibida_pedido(cur, recepcion['pedido_id'])
                _recalcular_estado_pedido(cur, recepcion['pedido_id'], empresa_id)

            conn.commit()
            flash('Recepción modificada correctamente. El stock se ha ajustado por diferencia.', 'success')
            return redirect(url_for('compras.validar_recepciones'))

        except Exception as e:
            conn.rollback()
            flash(f'Error modificando recepción: {str(e)}', 'danger')
            return redirect(url_for('compras.editar_recepcion', recepcion_id=recepcion_id))
        finally:
            conn.close()

    lineas = conn.execute("""
        SELECT lr.*, p.nombre AS producto_nombre, p.codigo AS producto_codigo, p.unidad,
               a.nombre AS almacen_nombre
        FROM lineas_recepcion lr
        JOIN productos p ON p.id = lr.producto_id
        LEFT JOIN almacenes a ON a.id = lr.almacen_id
        WHERE lr.recepcion_id = ?
          AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
        ORDER BY lr.id
    """, (recepcion_id, empresa_id)).fetchall()

    conn.close()
    return render_template(
        'compras/editar_recepcion.html',
        recepcion=recepcion,
        lineas=lineas,
        almacenes=almacenes,
        now=datetime.now()
    )

@compras_bp.route('/recepciones/validar')
@login_required
def validar_recepciones():
    conn = get_db(); empresa_id = session.get('user_empresa_id', 1); cur = conn.cursor(); _asegurar_estructura_compras(cur); conn.commit()
    proveedor_id = request.args.get('proveedor', '').strip()
    departamento_id = request.args.get('departamento', '').strip()
    estado_filtro = request.args.get('estado', '').strip()
    fecha_desde = request.args.get('fecha_desde', '').strip()
    fecha_hasta = request.args.get('fecha_hasta', '').strip()
    busqueda = request.args.get('buscar', '').strip()
    simulacion = request.args.get('simulacion', '').strip()
    scope_sql, scope_params, centro_activo_id = _scope_recepciones_centro(conn, 'r', empresa_id)
    mostrar_laboratorio_hotel1 = (
        simulacion == 'hotel1'
        or (int(empresa_id or 0) == 1 and int(centro_activo_id or 0) == 3)
    )
    filtros_sql = [scope_sql]
    params = list(scope_params)
    if proveedor_id:
        filtros_sql.append("r.proveedor_id = ?")
        params.append(proveedor_id)
    if departamento_id:
        filtros_sql.append("r.departamento_id = ?")
        params.append(departamento_id)
    if fecha_desde:
        filtros_sql.append("date(r.fecha_recepcion) >= date(?)")
        params.append(fecha_desde)
    if fecha_hasta:
        filtros_sql.append("date(r.fecha_recepcion) <= date(?)")
        params.append(fecha_hasta)
    if simulacion == 'hotel1':
        filtros_sql.append("(COALESCE(r.numero_albaran, '') LIKE ? OR COALESCE(p.numero_pedido, '') LIKE ?)")
        params.extend(['ALB-COMPRA-H1-%', 'COMPRA-H1-%'])
    if busqueda:
        like_busqueda = f"%{busqueda}%"
        filtros_sql.append("""(
            COALESCE(r.numero_albaran, '') LIKE ?
            OR COALESCE(p.numero_pedido, '') LIKE ?
            OR COALESCE(pr.nombre, '') LIKE ?
            OR COALESCE(d.nombre, '') LIKE ?
        )""")
        params.extend([like_busqueda, like_busqueda, like_busqueda, like_busqueda])
    where_sql = " AND ".join(filtros_sql)

    recepciones_rows = conn.execute(f"""
        SELECT r.*, p.numero_pedido, p.estado AS pedido_estado, pr.nombre AS proveedor_nombre,
               pr.documento_habitual, pr.facturacion_habitual,
               d.nombre AS departamento_nombre, dc.id AS documento_id, dc.nombre_archivo,
               (SELECT COUNT(*) FROM lineas_recepcion lr WHERE lr.recepcion_id = r.id) AS num_lineas,
               (SELECT COUNT(*) FROM lineas_recepcion lr WHERE lr.recepcion_id = r.id AND lr.incidencia_id IS NOT NULL) AS num_incidencias,
               (SELECT COALESCE(SUM(COALESCE(lr.cantidad_recibida, 0) * COALESCE(lp.precio_unitario, 0)), 0)
                FROM lineas_recepcion lr
                LEFT JOIN lineas_pedido lp ON lp.pedido_id = r.pedido_id AND lp.producto_id = lr.producto_id
                WHERE lr.recepcion_id = r.id) AS importe_pedido_recibido,
               (SELECT COALESCE(SUM(COALESCE(lr.importe_total, COALESCE(lr.cantidad_recibida, 0) * COALESCE(lr.precio_unitario, 0))), 0)
                FROM lineas_recepcion lr
                WHERE lr.recepcion_id = r.id) AS importe_recepcion,
               (SELECT COUNT(*)
                FROM lineas_recepcion lr
                JOIN lineas_pedido lp ON lp.pedido_id = r.pedido_id AND lp.producto_id = lr.producto_id
                WHERE lr.recepcion_id = r.id
                  AND lr.precio_unitario IS NOT NULL
                  AND lp.precio_unitario IS NOT NULL
                  AND ABS(COALESCE(lr.precio_unitario, 0) - COALESCE(lp.precio_unitario, 0)) > MAX(0.01, ABS(COALESCE(lp.precio_unitario, 0)) * 0.05)
               ) AS num_diferencias_precio,
               (SELECT COUNT(*)
                FROM documento_compra_lineas_detectadas dl
                JOIN documentos_compra doc ON doc.id = dl.documento_id
                WHERE (doc.recepcion_id = r.id OR doc.id = r.documento_adjunto_id)
                  AND dl.producto_id IS NULL
               ) AS pendientes_ocr
        FROM recepciones r
        LEFT JOIN pedidos p ON r.pedido_id = p.id
        LEFT JOIN proveedores pr ON r.proveedor_id = pr.id
        LEFT JOIN departamentos d ON d.id = r.departamento_id
        LEFT JOIN documentos_compra dc ON dc.id = r.documento_adjunto_id
        WHERE {where_sql}
        ORDER BY r.id DESC
    """, params).fetchall()
    recepciones = []
    resumen = {
        'total': 0,
        'incidencias': 0,
        'ocr': 0,
        'fisico': 0,
        'precios': 0,
        'validado': 0,
        'cerrado': 0,
        'rapido': 0,
    }
    for row in recepciones_rows:
        r = dict(row)
        diagnostico = _diagnostico_recepcion_economica(cur, r['id'], empresa_id)
        r['pendientes_ocr'] = diagnostico['pendientes_ocr']
        r['num_sin_precio'] = diagnostico['sin_precio']
        r['num_diferencias_precio'] = diagnostico['diferencias_precio']
        r['incidencias_abiertas'] = diagnostico['incidencias_abiertas']
        r['puede_validar_rapido'] = diagnostico['puede_validar_rapido']
        r['diferencia_importe'] = float(r.get('importe_recepcion') or 0) - float(r.get('importe_pedido_recibido') or 0)
        if r.get('num_incidencias', 0):
            r['estado_unificado'] = 'Con incidencias'
            r['estado_clase'] = 'bg-danger'
            r['estado_codigo'] = 'incidencias'
        elif r.get('pendientes_ocr', 0):
            r['estado_unificado'] = 'Pendiente asociar OCR'
            r['estado_clase'] = 'bg-warning text-dark'
            r['estado_codigo'] = 'ocr'
        elif not r.get('stock_aplicado') or not r.get('validada_operativamente'):
            r['estado_unificado'] = 'Recibido fisico'
            r['estado_clase'] = 'bg-info text-dark'
            r['estado_codigo'] = 'fisico'
        elif not r.get('validada_economicamente') or r.get('num_sin_precio', 0):
            r['estado_unificado'] = 'Pendiente precios'
            r['estado_clase'] = 'bg-warning text-dark'
            r['estado_codigo'] = 'precios'
        elif (r.get('pedido_estado') or '').lower() == 'cerrado':
            r['estado_unificado'] = 'Cerrado'
            r['estado_clase'] = 'bg-dark'
            r['estado_codigo'] = 'cerrado'
        elif r.get('num_diferencias_precio', 0):
            r['estado_unificado'] = 'Validado con diferencias'
            r['estado_clase'] = 'bg-info text-dark'
            r['estado_codigo'] = 'validado'
        else:
            r['estado_unificado'] = 'Validado economico'
            r['estado_clase'] = 'bg-success'
            r['estado_codigo'] = 'validado'

        resumen['total'] += 1
        if r['estado_codigo'] in resumen:
            resumen[r['estado_codigo']] += 1
        if r.get('puede_validar_rapido') and not r.get('validada_economicamente'):
            resumen['rapido'] += 1

        if estado_filtro:
            if estado_filtro == 'rapido':
                if not (r.get('puede_validar_rapido') and not r.get('validada_economicamente')):
                    continue
            elif r['estado_codigo'] != estado_filtro:
                continue
        recepciones.append(r)
    ver_precios = _usuario_puede_ver_precios_compras()
    proveedores = proveedores_select(conn, empresa_id)
    departamentos = _departamentos_visibles_compras(conn, empresa_id)
    filtros = {
        'proveedor': proveedor_id,
        'departamento': departamento_id,
        'estado': estado_filtro,
        'fecha_desde': fecha_desde,
        'fecha_hasta': fecha_hasta,
        'buscar': busqueda,
        'simulacion': simulacion,
    }
    resumen_simulacion_recepciones = conn.execute(f"""
        SELECT COUNT(*) AS total,
               MIN(date(r.fecha_recepcion)) AS fecha_desde,
               MAX(date(r.fecha_recepcion)) AS fecha_hasta,
               SUM(CASE WHEN COALESCE(r.validada_operativamente, 0) = 1 THEN 1 ELSE 0 END) AS operativas,
               SUM(CASE WHEN COALESCE(r.validada_economicamente, 0) = 1 THEN 1 ELSE 0 END) AS economicas,
               COALESCE(SUM((
                   SELECT COALESCE(SUM(COALESCE(lr.cantidad_recibida, 0) * COALESCE(lr.precio_unitario, 0)), 0)
                   FROM lineas_recepcion lr
                   WHERE lr.recepcion_id = r.id
               )), 0) AS importe
        FROM recepciones r
        LEFT JOIN pedidos p ON p.id = r.pedido_id
        WHERE {scope_sql}
          AND (COALESCE(r.numero_albaran, '') LIKE 'ALB-COMPRA-H1-%'
               OR COALESCE(p.numero_pedido, '') LIKE 'COMPRA-H1-%')
    """, scope_params).fetchone()
    conn.close(); return render_template('compras/validar_recepciones.html', recepciones=recepciones, ver_precios=ver_precios, proveedores=proveedores, departamentos=departamentos, filtros=filtros, resumen=resumen, resumen_simulacion_recepciones=resumen_simulacion_recepciones, mostrar_laboratorio_hotel1=mostrar_laboratorio_hotel1, centro_activo_id=centro_activo_id)


@compras_bp.route('/incidencias')
@login_required
def incidencias_compras():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    scope_sql, scope_params = _scope_departamento('r', empresa_id)
    incidencias = []

    rows_appcc = conn.execute(f"""
        SELECT i.id AS incidencia_id, i.descripcion, i.gravedad, i.estado,
               r.id AS recepcion_id, r.numero_albaran, r.fecha_recepcion,
               p.numero_pedido, pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre,
               prod.nombre AS producto_nombre
        FROM incidencias i
        JOIN lineas_recepcion lr ON lr.incidencia_id = i.id
        JOIN recepciones r ON r.id = lr.recepcion_id
        LEFT JOIN pedidos p ON p.id = r.pedido_id
        LEFT JOIN proveedores pr ON pr.id = r.proveedor_id
        LEFT JOIN departamentos d ON d.id = r.departamento_id
        LEFT JOIN productos prod ON prod.id = lr.producto_id
        WHERE i.tipo = 'recepcion'
          AND COALESCE(i.estado, 'abierta') != 'resuelta'
          AND {scope_sql}
        ORDER BY i.id DESC
        LIMIT 200
    """, scope_params).fetchall()
    for row in rows_appcc:
        incidencias.append({
            'tipo': 'Incidencia recepcion',
            'prioridad': row['gravedad'] or 'media',
            'estado': row['estado'] or 'abierta',
            'referencia': row['numero_pedido'] or f"Recepcion {row['recepcion_id']}",
            'proveedor': row['proveedor_nombre'] or '-',
            'departamento': row['departamento_nombre'] or '-',
            'detalle': f"{row['producto_nombre'] or 'Producto'} - {row['descripcion'] or ''}",
            'accion_url': url_for(
                'incidencias.ver',
                incidencia_id=row['incidencia_id'],
                next=url_for('compras.incidencias_compras'),
            ),
            'accion_texto': 'Resolver',
            'accion_secundaria_url': url_for('compras.editar_recepcion', recepcion_id=row['recepcion_id']),
            'accion_secundaria_texto': 'Recepcion',
        })

    rows_generales = conn.execute(f"""
        SELECT i.id AS incidencia_id, i.descripcion, i.gravedad, i.estado,
               i.fecha, i.ubicacion, i.categoria_operativa,
               i.departamento_id AS departamento_id,
               d.nombre AS departamento_nombre
        FROM incidencias i
        LEFT JOIN departamentos d ON d.id = i.departamento_id
        WHERE i.empresa_id = ?
          AND COALESCE(i.destino_modulo, '') = 'compras'
          AND COALESCE(i.estado, 'abierta') != 'resuelta'
          AND NOT EXISTS (
              SELECT 1 FROM lineas_recepcion lr WHERE lr.incidencia_id = i.id
          )
        ORDER BY i.id DESC
        LIMIT 100
    """, (empresa_id,)).fetchall()
    for row in rows_generales:
        if not usuario_puede_ver_departamento(row['departamento_id'], ROLES_COMPRAS_GLOBAL):
            continue
        incidencias.append({
            'tipo': 'Incidencia compras',
            'prioridad': row['gravedad'] or 'media',
            'estado': row['estado'] or 'abierta',
            'referencia': row['ubicacion'] or f"Incidencia {row['incidencia_id']}",
            'proveedor': '-',
            'departamento': row['departamento_nombre'] or '-',
            'detalle': f"{row['categoria_operativa'] or 'general'} - {row['descripcion'] or ''}",
            'accion_url': url_for(
                'incidencias.ver',
                incidencia_id=row['incidencia_id'],
                next=url_for('compras.incidencias_compras'),
            ),
            'accion_texto': 'Resolver',
        })

    rows_precios = conn.execute(f"""
        SELECT r.id AS recepcion_id, r.numero_albaran, p.numero_pedido,
               pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre,
               prod.nombre AS producto_nombre,
               lp.precio_unitario AS precio_pedido,
               lr.precio_unitario AS precio_factura
        FROM lineas_recepcion lr
        JOIN recepciones r ON r.id = lr.recepcion_id
        JOIN lineas_pedido lp ON lp.pedido_id = r.pedido_id AND lp.producto_id = lr.producto_id
        LEFT JOIN pedidos p ON p.id = r.pedido_id
        LEFT JOIN proveedores pr ON pr.id = r.proveedor_id
        LEFT JOIN departamentos d ON d.id = r.departamento_id
        LEFT JOIN productos prod ON prod.id = lr.producto_id
        WHERE {scope_sql}
          AND COALESCE(r.validada_economicamente, 0) = 0
          AND lr.precio_unitario IS NOT NULL
          AND lp.precio_unitario IS NOT NULL
          AND ABS(COALESCE(lr.precio_unitario, 0) - COALESCE(lp.precio_unitario, 0)) > MAX(0.01, ABS(COALESCE(lp.precio_unitario, 0)) * 0.05)
        ORDER BY r.id DESC
        LIMIT 200
    """, scope_params).fetchall()
    for row in rows_precios:
        diferencia = float(row['precio_factura'] or 0) - float(row['precio_pedido'] or 0)
        incidencias.append({
            'tipo': 'Diferencia precio',
            'prioridad': 'alta' if diferencia > 0 else 'media',
            'estado': 'pendiente',
            'referencia': row['numero_pedido'] or f"Recepcion {row['recepcion_id']}",
            'proveedor': row['proveedor_nombre'] or '-',
            'departamento': row['departamento_nombre'] or '-',
            'detalle': f"{row['producto_nombre']}: pedido {row['precio_pedido'] or 0:.4f} EUR / factura {row['precio_factura'] or 0:.4f} EUR / dif {diferencia:+.4f} EUR",
            'accion_url': url_for('compras.pantalla_validacion_economica', id=row['recepcion_id']),
            'accion_texto': 'Validar precios',
        })

    doc_scope = ""
    doc_params = [empresa_id]
    if not usuario_es_global_operacion(ROLES_COMPRAS_GLOBAL):
        doc_scope = f" AND r.id IS NOT NULL AND {scope_sql}"
        doc_params.extend(scope_params)

    rows_ocr = conn.execute(f"""
        SELECT dl.id AS linea_id, dl.descripcion_detectada, doc.id AS documento_id, doc.nombre_archivo,
               r.id AS recepcion_id, pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre
        FROM documento_compra_lineas_detectadas dl
        JOIN documentos_compra doc ON doc.id = dl.documento_id
        LEFT JOIN recepciones r ON r.id = doc.recepcion_id
        LEFT JOIN proveedores pr ON pr.id = COALESCE(doc.proveedor_id, r.proveedor_id)
        LEFT JOIN departamentos d ON d.id = r.departamento_id
        WHERE dl.producto_id IS NULL
          AND (r.empresa_id = ? OR r.empresa_id IS NULL OR r.id IS NULL)
          {doc_scope}
        ORDER BY dl.id DESC
        LIMIT 200
    """, doc_params).fetchall()
    for row in rows_ocr:
        incidencias.append({
            'tipo': 'OCR sin producto',
            'prioridad': 'media',
            'estado': 'pendiente',
            'referencia': row['nombre_archivo'] or f"Documento {row['documento_id']}",
            'proveedor': row['proveedor_nombre'] or '-',
            'departamento': row['departamento_nombre'] or '-',
            'detalle': row['descripcion_detectada'] or 'Linea sin descripcion',
            'accion_url': url_for('compras.revisar_lineas_detectadas', documento_id=row['documento_id']),
            'accion_texto': 'Asociar producto',
        })

    rows_documento = conn.execute(f"""
        SELECT r.id AS recepcion_id, r.numero_albaran, r.fecha_recepcion,
               p.numero_pedido, pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre
        FROM recepciones r
        LEFT JOIN pedidos p ON p.id = r.pedido_id
        LEFT JOIN proveedores pr ON pr.id = r.proveedor_id
        LEFT JOIN departamentos d ON d.id = r.departamento_id
        WHERE {scope_sql}
          AND r.documento_adjunto_id IS NULL
        ORDER BY r.id DESC
        LIMIT 100
    """, scope_params).fetchall()
    for row in rows_documento:
        incidencias.append({
            'tipo': 'Sin documento',
            'prioridad': 'baja',
            'estado': 'pendiente',
            'referencia': row['numero_pedido'] or f"Recepcion {row['recepcion_id']}",
            'proveedor': row['proveedor_nombre'] or '-',
            'departamento': row['departamento_nombre'] or '-',
            'detalle': f"Recepcion sin albaran/factura adjunta. Albaran: {row['numero_albaran'] or '-'}",
            'accion_url': url_for('compras.editar_recepcion', recepcion_id=row['recepcion_id']),
            'accion_texto': 'Adjuntar',
        })

    rows_stock = conn.execute("""
        SELECT sa.producto_id, sa.almacen_id, sa.cantidad, sa.stock_minimo,
               p.nombre AS producto_nombre, p.stock_minimo AS minimo_producto,
               a.nombre AS almacen_nombre, d.nombre AS departamento_nombre,
               a.departamento_id AS departamento_id
        FROM stock_almacen sa
        JOIN productos p ON p.id = sa.producto_id
        LEFT JOIN almacenes a ON a.id = sa.almacen_id
        LEFT JOIN departamentos d ON d.id = a.departamento_id
        WHERE p.empresa_id = ?
          AND COALESCE(p.activo, 1) = 1
          AND COALESCE(sa.cantidad, 0) <= COALESCE(NULLIF(sa.stock_minimo, 0), NULLIF(p.stock_minimo, 0), 0)
          AND COALESCE(NULLIF(sa.stock_minimo, 0), NULLIF(p.stock_minimo, 0), 0) > 0
        ORDER BY p.nombre
        LIMIT 200
    """, (empresa_id,)).fetchall()
    for row in rows_stock:
        if not usuario_puede_ver_departamento(row['departamento_id'], ROLES_COMPRAS_GLOBAL):
            continue
        minimo = row['stock_minimo'] or row['minimo_producto'] or 0
        incidencias.append({
            'tipo': 'Stock bajo',
            'prioridad': 'alta',
            'estado': 'pendiente',
            'referencia': row['almacen_nombre'] or 'Almacen',
            'proveedor': '-',
            'departamento': row['departamento_nombre'] or '-',
            'detalle': f"{row['producto_nombre']}: stock {row['cantidad'] or 0:.3f} / minimo {minimo:.3f}",
            'accion_url': url_for('stock.stock'),
            'accion_texto': 'Ver stock',
        })

    rows_caducidad = conn.execute("""
        SELECT l.id, l.codigo_lote, l.fecha_caducidad, l.cantidad_actual,
               p.nombre AS producto_nombre, a.nombre AS almacen_nombre,
               d.nombre AS departamento_nombre, d.id AS departamento_id,
               CAST(julianday(l.fecha_caducidad) - julianday(date('now')) AS INTEGER) AS dias
        FROM lotes l
        JOIN productos p ON p.id = l.producto_id
        LEFT JOIN almacenes a ON a.id = l.almacen_id
        LEFT JOIN departamentos d ON d.id = a.departamento_id
        WHERE l.empresa_id = ?
          AND l.fecha_caducidad IS NOT NULL
          AND COALESCE(l.cantidad_actual, 0) > 0
          AND date(l.fecha_caducidad) <= date('now', '+15 days')
        ORDER BY l.fecha_caducidad ASC
        LIMIT 200
    """, (empresa_id,)).fetchall()
    for row in rows_caducidad:
        if not usuario_puede_ver_departamento(row['departamento_id'], ROLES_COMPRAS_GLOBAL):
            continue
        prioridad = 'alta' if (row['dias'] or 0) <= 3 else 'media'
        incidencias.append({
            'tipo': 'Caducidad proxima',
            'prioridad': prioridad,
            'estado': 'pendiente',
            'referencia': row['codigo_lote'] or f"Lote {row['id']}",
            'proveedor': '-',
            'departamento': row['departamento_nombre'] or '-',
            'detalle': f"{row['producto_nombre']}: caduca {row['fecha_caducidad']} ({row['dias']} dias), cantidad {row['cantidad_actual'] or 0:.3f}",
            'accion_url': url_for('lotes.lista_lotes'),
            'accion_texto': 'Ver lotes',
        })

    prioridad_orden = {'alta': 0, 'media': 1, 'baja': 2}
    incidencias.sort(key=lambda item: (prioridad_orden.get((item['prioridad'] or '').lower(), 9), item['tipo']))
    conn.close()
    return render_template('compras/incidencias.html', incidencias=incidencias)


@compras_bp.route('/auditoria')
@login_required
def auditoria_compras():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    rows = conn.execute("""
        SELECT a.*, p.numero_pedido, r.numero_albaran, pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre
        FROM compras_auditoria a
        LEFT JOIN pedidos p ON a.referencia_tipo = 'pedido' AND p.id = a.referencia_id
        LEFT JOIN recepciones r ON a.referencia_tipo = 'recepcion' AND r.id = a.referencia_id
        LEFT JOIN proveedores pr ON pr.id = COALESCE(a.proveedor_id, p.proveedor_id, r.proveedor_id)
        LEFT JOIN departamentos d ON d.id = COALESCE(a.departamento_id, p.departamento_id, r.departamento_id)
        WHERE a.empresa_id = ?
        ORDER BY a.id DESC
        LIMIT 500
    """, (empresa_id,)).fetchall()
    conn.close()
    return render_template('compras/auditoria.html', eventos=rows)


@compras_bp.route('/precios/historico')
@login_required
def historico_precios():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    producto_id = _to_int_or_none(request.args.get('producto_id'))
    proveedor_id = _to_int_or_none(request.args.get('proveedor_id'))
    params = [empresa_id]
    filtros = "WHERE h.empresa_id = ?"
    if producto_id:
        filtros += " AND h.producto_id = ?"
        params.append(producto_id)
    if proveedor_id:
        filtros += " AND h.proveedor_id = ?"
        params.append(proveedor_id)

    historico = conn.execute(f"""
        SELECT h.*, p.nombre AS producto_nombre, p.codigo AS producto_codigo, pr.nombre AS proveedor_nombre
        FROM compras_historico_precios h
        JOIN productos p ON p.id = h.producto_id
        LEFT JOIN proveedores pr ON pr.id = h.proveedor_id
        {filtros}
        ORDER BY h.fecha DESC, h.id DESC
        LIMIT 500
    """, params).fetchall()

    productos = conn.execute("""
        SELECT DISTINCT p.id, p.nombre
        FROM productos p
        JOIN compras_historico_precios h ON h.producto_id = p.id
        WHERE h.empresa_id = ?
        ORDER BY p.nombre
    """, (empresa_id,)).fetchall()
    proveedores = proveedores_select(conn, empresa_id)
    conn.close()
    return render_template(
        'compras/historico_precios.html',
        historico=historico,
        productos=productos,
        proveedores=proveedores,
        filtros={'producto_id': producto_id, 'proveedor_id': proveedor_id},
    )


@compras_bp.route('/recepciones/<int:id>/validar-operativa', methods=['POST'])
@login_required
def validar_operativa(id):
    conn = get_db(); empresa_id = session.get('user_empresa_id', 1); usuario = (session.get('user_nombre') or 'admin').strip()
    try:
        cur = conn.cursor()
        scope_rx_sql, scope_rx_params, _centro_id = _scope_recepciones_centro(conn, "r", empresa_id)
        recepcion_permiso = conn.execute(
            f"SELECT r.departamento_id FROM recepciones r WHERE r.id = ? AND {scope_rx_sql}",
            [id] + scope_rx_params,
        ).fetchone()
        if not recepcion_permiso:
            conn.rollback(); flash('Recepcion no encontrada.', 'danger'); return redirect(url_for('compras.validar_recepciones'))
        if not usuario_puede_recepcionar_compras_departamento(recepcion_permiso['departamento_id']):
            conn.rollback(); flash('No tienes permiso para validar la recepcion operativa de este departamento.', 'warning'); return redirect(url_for('compras.validar_recepciones'))
        cur.execute(
            f"UPDATE recepciones SET validada_operativamente = 1 WHERE id = ? AND id IN (SELECT r.id FROM recepciones r WHERE {scope_rx_sql})",
            [id] + scope_rx_params,
        )
        if cur.rowcount == 0:
            conn.rollback(); flash('Recepcion no encontrada o sin permisos.', 'danger'); return redirect(url_for('compras.validar_recepciones'))
        cur.execute("""INSERT INTO recepcion_validaciones (recepcion_id, tipo_validacion, estado, fecha, observaciones) VALUES (?, 'operativa', 'validada', datetime('now'), ?)""", (id, f'Validada operativamente por {usuario}'))
        aplicadas = _aplicar_stock_recepcion(cur, id, empresa_id, usuario)
        _registrar_auditoria_compra(cur, empresa_id, 'recepcion_validada_operativamente', referencia_tipo='recepcion', referencia_id=id, detalle=f'Stock fisico aplicado en {aplicadas} lineas pendientes', departamento_id=recepcion_permiso['departamento_id'], estado_nuevo='validada_operativamente', usuario=usuario)
        conn.commit(); flash(f'Recepcion validada operativamente. Stock fisico aplicado en {aplicadas} linea(s) pendientes.', 'success')
        return redirect(url_for('compras.validar_recepciones'))
    except Exception as e:
        conn.rollback(); flash(f'Error validando recepción operativa: {str(e)}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('compras.validar_recepciones'))

@compras_bp.route('/recepciones/<int:id>/validar-economica', methods=['POST'])
@login_required
def validar_economica(id):
    conn = get_db(); empresa_id = session.get('user_empresa_id', 1); usuario = (session.get('user_nombre') or 'admin').strip()
    if not usuario_es_global_operacion(ROLES_COMPRAS_GLOBAL):
        conn.close(); flash('La validacion economica queda reservada a compras, administracion o direccion.', 'warning'); return redirect(url_for('compras.validar_recepciones'))
    try:
        cur = conn.cursor(); _asegurar_estructura_compras(cur); _asegurar_columna_recepcion_stock_aplicado(cur)
        scope_rx_sql, scope_rx_params, centro_id = _scope_recepciones_centro(conn, "r", empresa_id)
        recepcion = conn.execute(
            f"SELECT r.* FROM recepciones r WHERE r.id = ? AND {scope_rx_sql}",
            [id] + scope_rx_params,
        ).fetchone()
        if not recepcion:
            conn.rollback(); flash('Recepcion no encontrada.', 'danger'); return redirect(url_for('compras.validar_recepciones'))
        diagnostico = _diagnostico_recepcion_economica(cur, id, empresa_id)
        if not diagnostico['puede_validar_rapido']:
            motivos = []
            if diagnostico['pendientes_ocr']:
                motivos.append(f"{diagnostico['pendientes_ocr']} linea(s) OCR sin producto")
            if diagnostico['sin_precio']:
                motivos.append(f"{diagnostico['sin_precio']} linea(s) sin precio")
            if diagnostico['diferencias_precio']:
                motivos.append(f"{diagnostico['diferencias_precio']} diferencia(s) de precio")
            if diagnostico['incidencias_abiertas']:
                motivos.append(f"{diagnostico['incidencias_abiertas']} incidencia(s) abierta(s)")
            conn.rollback()
            flash('No se puede validar rapido: ' + ', '.join(motivos) + '. Revisa la pantalla de precios.', 'warning')
            return redirect(url_for('compras.pantalla_validacion_economica', id=id))
        proveedor_pref = conn.execute("""
            SELECT documento_habitual, facturacion_habitual, forma_pago, dias_pago
            FROM proveedores
            WHERE id = ? AND empresa_id = ?
        """, (recepcion['proveedor_id'], empresa_id)).fetchone()
        documento_tipo = recepcion['documento_economico_tipo'] or (proveedor_pref['documento_habitual'] if proveedor_pref else None) or 'albaran'
        forma_pago = recepcion['forma_pago_economica'] or (proveedor_pref['forma_pago'] if proveedor_pref else None) or 'transferencia'
        dias_pago = proveedor_pref['dias_pago'] if proveedor_pref else 30
        fecha_vencimiento = recepcion['fecha_vencimiento_economica'] or _fecha_vencimiento_compra(recepcion['fecha_recepcion'], dias_pago)
        estado_facturacion = _estado_facturacion_compra(documento_tipo)
        cur.execute("""
            UPDATE recepciones
            SET validada_economicamente = 1,
                documento_economico_tipo = ?,
                estado_facturacion = ?,
                fecha_vencimiento_economica = ?,
                forma_pago_economica = ?
            WHERE id = ? AND empresa_id = ?
        """, (documento_tipo, estado_facturacion, fecha_vencimiento, forma_pago, id, empresa_id))
        cur.execute("""INSERT INTO recepcion_validaciones (recepcion_id, tipo_validacion, estado, fecha, observaciones) VALUES (?, 'economica', 'validada', datetime('now'), ?)""", (id, f'Validada economicamente por {usuario} como {documento_tipo}'))
        aplicadas = _aplicar_stock_recepcion(cur, id, empresa_id, usuario)
        if recepcion:
            _registrar_auditoria_compra(cur, empresa_id, 'recepcion_validada_economicamente', referencia_tipo='recepcion', referencia_id=id, detalle=f'Validacion rapida como {documento_tipo}. Facturacion: {estado_facturacion}. Stock pendiente aplicado: {aplicadas}', departamento_id=recepcion['departamento_id'], proveedor_id=recepcion['proveedor_id'], estado_nuevo='validada_economicamente', usuario=usuario)
        conn.commit(); flash(f'Recepcion validada economicamente como {documento_tipo}. Lineas aplicadas a stock: {aplicadas}.', 'success')
    except Exception as e:
        conn.rollback(); flash(f'Error validando económicamente: {str(e)}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('compras.validar_recepciones')) 
@compras_bp.route('/documentos-compra/<int:documento_id>/validar-economico', methods=['POST'])
@login_required
def validar_economico(documento_id):
    conn = get_db()
    cursor = conn.cursor()
    empresa_id = session.get('user_empresa_id', 1)
    if not usuario_es_global_operacion(ROLES_COMPRAS_GLOBAL):
        conn.close()
        flash('La validacion economica queda reservada a compras, administracion o direccion.', 'warning')
        return redirect(url_for('compras.validar_recepciones'))

    try:
        documento = conn.execute("""
            SELECT *
            FROM documentos_compra
            WHERE id = ?
              AND empresa_id = ?
        """, (documento_id, empresa_id)).fetchone()
        if not documento:
            conn.close()
            flash('Documento no encontrado.', 'danger')
            return redirect(url_for('compras.validar_recepciones'))
        centro_id = _centro_id_desde_documento_compra(conn, documento_id, empresa_id)

        proveedor_id = documento['proveedor_id']
        if not proveedor_id and documento['recepcion_id']:
            recepcion_doc = conn.execute("""
                SELECT proveedor_id
                FROM recepciones
                WHERE id = ?
                  AND empresa_id = ?
            """, (documento['recepcion_id'], empresa_id)).fetchone()
            proveedor_id = recepcion_doc['proveedor_id'] if recepcion_doc else None

        if not proveedor_id:
            conn.close()
            flash('No se puede validar precios sin proveedor asociado al documento.', 'warning')
            return redirect(url_for('compras.validar_recepciones'))

        lineas = conn.execute("""
            SELECT *
            FROM documento_compra_lineas_detectadas
            WHERE documento_id = ?
              AND producto_id IS NOT NULL
              AND precio_detectado IS NOT NULL
              AND precio_detectado > 0
        """, (documento_id,)).fetchall()

        actualizadas = 0
        for l in lineas:
            precio = _to_float_safe(l['precio_detectado'], None)
            producto_id = l['producto_id']
            if precio is None or precio <= 0:
                continue

            if _actualizar_precio_producto_proveedor_compra(
                cursor,
                empresa_id,
                producto_id,
                proveedor_id,
                precio,
                origen='documento_compra',
                referencia_tipo='documento_compra',
                referencia_id=documento_id,
                centro_id=centro_id,
            ):
                actualizadas += 1

        _registrar_auditoria_compra(cursor, empresa_id, 'precios_documento_validados', referencia_tipo='documento_compra', referencia_id=documento_id, detalle=f'{actualizadas} precios validados', proveedor_id=proveedor_id)
        conn.commit()
        conn.close()
        flash(f'Precios del documento validados: {actualizadas} linea(s).', 'success')
        return redirect(url_for('compras.validar_recepciones'))

    except Exception as e:
        conn.rollback()
        conn.close()
        flash(f'Error validando precios del documento: {str(e)}', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

@compras_bp.route('/recepciones/<int:id>/detalle')
@login_required
def detalle_validacion_recepcion(id):
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = _centro_activo_id()
    if not centro_id:
        flash('Selecciona un centro antes de revisar la validacion de recepcion.', 'warning')
        return redirect(url_for('rrhh.centro_empresa_general'))
    return redirect(url_for('compras.pantalla_validacion_economica', id=id))
    
@compras_bp.route('/compras/guardar-validacion-economica', methods=['POST'])
@login_required
def guardar_validacion_economica():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = _centro_activo_id()
    if not usuario_es_global_operacion(ROLES_COMPRAS_GLOBAL):
        conn.close()
        flash('La validacion economica queda reservada a compras, administracion o direccion.', 'warning')
        return redirect(url_for('compras.validar_recepciones'))
    if not centro_id:
        conn.close()
        flash('Selecciona un centro antes de validar compras.', 'warning')
        return redirect(url_for('rrhh.centro_empresa_general'))

    conn.close()
    flash('La pantalla antigua de validacion economica se ha retirado. Usa la validacion de la recepcion.', 'info')
    return redirect(url_for('compras.validar_recepciones'))



@compras_bp.route('/recepciones/<int:id>/guardar-validacion-economica', methods=['POST'])
@login_required
def guardar_validacion_economica_detalle(id):
    conn = get_db()
    cursor = conn.cursor()
    empresa_id = session.get('user_empresa_id', 1)
    if not usuario_es_global_operacion(ROLES_COMPRAS_GLOBAL):
        conn.close()
        flash('La validacion economica queda reservada a compras, administracion o direccion.', 'warning')
        return redirect(url_for('compras.validar_recepciones'))

    def _split_linea_validacion(valor):
        texto = str(valor or '').strip()
        if texto.startswith('doc_'):
            return 'doc', _to_int_or_none(texto[4:])
        if texto.startswith('lr_'):
            return 'recepcion', _to_int_or_none(texto[3:])
        return 'doc', _to_int_or_none(texto)

    def _guardar_precio_proveedor(producto_id, proveedor_id, precio_final):
        return _actualizar_precio_producto_proveedor_compra(
            cursor,
            empresa_id,
            producto_id,
            proveedor_id,
            precio_final,
            origen='validacion_economica',
            referencia_tipo='recepcion',
            referencia_id=id,
            centro_id=centro_id if 'centro_id' in locals() else None,
        )

    try:
        _asegurar_estructura_compras(cursor)
        scope_sql, scope_params, centro_id = _scope_recepciones_centro(conn, "r", empresa_id)
        recepcion = conn.execute("""
            SELECT *
            FROM recepciones r
            WHERE r.id = ?
              AND """ + scope_sql + """
        """, [id] + scope_params).fetchone()

        if not recepcion:
            flash('Recepcion no encontrada.', 'danger')
            conn.close()
            return redirect(url_for('compras.validar_recepciones'))

        proveedor_id = recepcion['proveedor_id']
        actualizadas = 0
        linea_ids = request.form.getlist('linea_id[]')

        for linea_token in linea_ids:
            origen, linea_pk = _split_linea_validacion(linea_token)
            if not linea_pk:
                continue

            precio_raw = (request.form.get(f'precio_final_{linea_token}') or '').strip()
            cantidad_raw = (request.form.get(f'cantidad_{linea_token}') or '').strip()
            precio_final = _to_float_safe(precio_raw, None)
            cantidad_final = _to_float_safe(cantidad_raw, None)

            if origen == 'doc':
                linea = conn.execute("""
                    SELECT dl.*
                    FROM documento_compra_lineas_detectadas dl
                    JOIN documentos_compra dc ON dc.id = dl.documento_id
                    WHERE dl.id = ?
                      AND dc.empresa_id = ?
                      AND (dc.recepcion_id = ? OR dc.id = COALESCE(?, 0))
                """, (
                    linea_pk,
                    empresa_id,
                    id,
                    recepcion['documento_adjunto_id'] if 'documento_adjunto_id' in recepcion.keys() else None,
                )).fetchone()
                if not linea:
                    continue

                cursor.execute("""
                    UPDATE documento_compra_lineas_detectadas
                    SET precio_detectado = COALESCE(?, precio_detectado),
                        cantidad_detectada = COALESCE(?, cantidad_detectada),
                        estado = CASE WHEN producto_id IS NULL THEN 'pendiente_producto' ELSE 'precio_validado' END
                    WHERE id = ?
                """, (precio_final, cantidad_final, linea_pk))

                producto_id = linea['producto_id']
                if producto_id and precio_final is not None and precio_final > 0:
                    cursor.execute("""
                        UPDATE lineas_recepcion
                        SET precio_unitario = ?,
                            importe_total = ROUND(COALESCE(cantidad_recibida, 0) * ?, 2)
                        WHERE recepcion_id = ?
                          AND producto_id = ?
                          AND (empresa_id = ? OR empresa_id IS NULL)
                    """, (precio_final, precio_final, id, producto_id, empresa_id))
                    if _actualizar_precio_producto_proveedor_compra(
                        cursor,
                        empresa_id,
                        producto_id,
                        proveedor_id,
                        precio_final,
                        origen='validacion_economica',
                        referencia_tipo='recepcion',
                        referencia_id=id,
                        centro_id=centro_id,
                    ):
                        actualizadas += 1
                continue

            linea = conn.execute("""
                SELECT *
                FROM lineas_recepcion
                WHERE id = ?
                  AND recepcion_id = ?
                  AND (empresa_id = ? OR empresa_id IS NULL)
            """, (linea_pk, id, empresa_id)).fetchone()
            if not linea:
                continue

            producto_id = linea['producto_id']
            if precio_final is not None and precio_final > 0:
                cantidad_base = cantidad_final if cantidad_final is not None else _to_float_safe(linea['cantidad_recibida'], 0.0)
                cursor.execute("""
                    UPDATE lineas_recepcion
                    SET precio_unitario = ?,
                        importe_total = ?
                    WHERE id = ?
                """, (precio_final, round(cantidad_base * precio_final, 2), linea_pk))
                if _actualizar_precio_producto_proveedor_compra(
                    cursor,
                    empresa_id,
                    producto_id,
                    proveedor_id,
                    precio_final,
                    origen='validacion_economica',
                    referencia_tipo='recepcion',
                    referencia_id=id,
                    centro_id=centro_id,
                ):
                    actualizadas += 1

        diagnostico_final = _diagnostico_recepcion_economica(cursor, id, empresa_id)
        bloqueos = []
        if diagnostico_final['pendientes_ocr']:
            bloqueos.append(f"{diagnostico_final['pendientes_ocr']} linea(s) OCR sin producto")
        if diagnostico_final['sin_precio']:
            bloqueos.append(f"{diagnostico_final['sin_precio']} linea(s) sin precio final")
        if diagnostico_final['incidencias_abiertas']:
            bloqueos.append(f"{diagnostico_final['incidencias_abiertas']} incidencia(s) abierta(s)")
        if bloqueos:
            conn.rollback()
            flash('No se puede cerrar la validacion economica: ' + ', '.join(bloqueos) + '.', 'warning')
            conn.close()
            return redirect(url_for('compras.pantalla_validacion_economica', id=id))

        documento_economico_tipo = (request.form.get('documento_economico_tipo') or 'albaran').strip()
        forma_pago_economica = (request.form.get('forma_pago_economica') or 'transferencia').strip()
        dias_pago = _to_int_or_none(request.form.get('dias_pago')) or 0
        fecha_vencimiento = (request.form.get('fecha_vencimiento_economica') or '').strip()
        if not fecha_vencimiento:
            fecha_vencimiento = _fecha_vencimiento_compra(recepcion['fecha_recepcion'], dias_pago)
        estado_facturacion = _estado_facturacion_compra(documento_economico_tipo)

        cursor.execute("""
            UPDATE recepciones
            SET validada_economicamente = 1,
                documento_economico_tipo = ?,
                estado_facturacion = ?,
                fecha_vencimiento_economica = ?,
                forma_pago_economica = ?
            WHERE id = ? AND empresa_id = ?
        """, (
            documento_economico_tipo,
            estado_facturacion,
            fecha_vencimiento,
            forma_pago_economica,
            id,
            empresa_id,
        ))

        if request.form.get('guardar_preferencia_proveedor') and proveedor_id:
            cursor.execute("""
                UPDATE proveedores
                SET documento_habitual = ?,
                    facturacion_habitual = ?,
                    forma_pago = ?,
                    dias_pago = ?
                WHERE id = ? AND empresa_id = ?
            """, (
                documento_economico_tipo,
                request.form.get('facturacion_habitual') or ('mensual' if documento_economico_tipo in ('albaran', 'factura_mensual') else 'inmediata'),
                forma_pago_economica,
                dias_pago,
                proveedor_id,
                empresa_id,
            ))

        cursor.execute("""
            INSERT INTO recepcion_validaciones (recepcion_id, tipo_validacion, estado, fecha, observaciones)
            VALUES (?, 'economica', 'validada', datetime('now'), ?)
        """, (id, f'Validada economicamente por {session.get("user_nombre") or "admin"} como {documento_economico_tipo}'))

        aplicadas = _aplicar_stock_recepcion(cursor, id, empresa_id, session.get('user_nombre') or 'admin')
        _registrar_auditoria_compra(
            cursor,
            empresa_id,
            'recepcion_validada_economicamente',
            referencia_tipo='recepcion',
            referencia_id=id,
            detalle=f'Precios revisados: {actualizadas}. Documento: {documento_economico_tipo}. Facturacion: {estado_facturacion}. Stock pendiente aplicado: {aplicadas}',
            departamento_id=recepcion['departamento_id'],
            proveedor_id=recepcion['proveedor_id'],
            estado_nuevo='validada_economicamente',
        )
        conn.commit()
        flash(f'Precios revisados: {actualizadas} linea(s). Recepcion validada como {documento_economico_tipo}. Stock pendiente aplicado: {aplicadas}.', 'success')
        conn.close()
        return redirect(url_for('compras.validar_recepciones'))

    except Exception as e:
        conn.rollback()
        flash(f'Error guardando validacion economica: {str(e)}', 'danger')
        conn.close()
        return redirect(url_for('compras.validar_recepciones'))

    try:
        recepcion = conn.execute("""
            SELECT *
            FROM recepciones
            WHERE id = ? AND empresa_id = ?
        """, (id, empresa_id)).fetchone()

        if not recepcion:
            flash('Recepción no encontrada.', 'danger')
            conn.close()
            return redirect(url_for('compras.validar_recepciones'))

        proveedor_id = recepcion['proveedor_id']

        linea_ids = request.form.getlist('linea_id[]')

        for linea_id in linea_ids:
            precio_raw = (request.form.get(f'precio_final_{linea_id}') or '').strip()
            cantidad_raw = (request.form.get(f'cantidad_{linea_id}') or '').strip()

            try:
                precio_final = float(precio_raw.replace(',', '.')) if precio_raw else None
            except Exception:
                precio_final = None

            try:
                cantidad_final = float(cantidad_raw.replace(',', '.')) if cantidad_raw else None
            except Exception:
                cantidad_final = None

            linea = conn.execute("""
                SELECT *
                FROM documento_compra_lineas_detectadas
                WHERE id = ?
            """, (linea_id,)).fetchone()

            if not linea:
                continue

            cursor.execute("""
                UPDATE documento_compra_lineas_detectadas
                SET precio_detectado = COALESCE(?, precio_detectado),
                    cantidad_detectada = COALESCE(?, cantidad_detectada),
                    estado = 'precio_validado'
                WHERE id = ?
            """, (precio_final, cantidad_final, linea_id))

            producto_id = linea['producto_id']
            if not producto_id or precio_final is None or precio_final <= 0:
                continue

            existente = cursor.execute("""
                SELECT id
                FROM productos_proveedores
                WHERE producto_id = ?
                  AND proveedor_id = ?
                LIMIT 1
            """, (producto_id, proveedor_id)).fetchone()

            if existente:
                cursor.execute("""
                    UPDATE productos_proveedores
                    SET precio_ultimo = ?,
                        precio_vigente = ?,
                        activo = 1
                    WHERE id = ?
                """, (precio_final, precio_final, existente['id']))
            else:
                cursor.execute("""
                    INSERT INTO productos_proveedores (
                        producto_id,
                        proveedor_id,
                        precio_ultimo,
                        precio_vigente,
                        activo,
                        preferente
                    )
                    VALUES (?, ?, ?, ?, 1, 0)
                """, (producto_id, proveedor_id, precio_final, precio_final))

        cursor.execute("""
            UPDATE recepciones
            SET validada_economicamente = 1
            WHERE id = ? AND empresa_id = ?
        """, (id, empresa_id))

        conn.commit()
        flash('Precios revisados y recepción validada económicamente.', 'success')

    except Exception as e:
        conn.rollback()
        flash(f'Error guardando validación económica: {str(e)}', 'danger')

    finally:
        conn.close()

    return redirect(url_for('compras.validar_recepciones'))

@compras_bp.route('/recepciones/<int:id>/validacion-economica')
@login_required
def pantalla_validacion_economica(id):
    """
    Pantalla económica: siempre debe mostrar líneas para validar precios.
    Prioridad:
    1) Líneas OCR guardadas en documento_compra_lineas_detectadas, si existen.
    2) Líneas reales de lineas_recepcion, aunque no haya OCR o el OCR no haya generado líneas económicas.
    """
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    if not usuario_es_global_operacion(ROLES_COMPRAS_GLOBAL):
        conn.close()
        flash('La validacion economica queda reservada a compras, administracion o direccion.', 'warning')
        return redirect(url_for('compras.validar_recepciones'))
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    scope_sql, scope_params, centro_id = _scope_recepciones_centro(conn, "r", empresa_id)
    recepcion = conn.execute("""
        SELECT r.*, pr.nombre AS proveedor_nombre, p.numero_pedido,
               pr.documento_habitual, pr.facturacion_habitual,
               pr.forma_pago AS proveedor_forma_pago,
               pr.dias_pago AS proveedor_dias_pago
        FROM recepciones r
        LEFT JOIN proveedores pr ON r.proveedor_id = pr.id
        LEFT JOIN pedidos p ON r.pedido_id = p.id
        WHERE r.id = ?
          AND """ + scope_sql + """
    """, [id] + scope_params).fetchone()

    if not recepcion:
        conn.close()
        flash('Recepción no encontrada.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    columnas_pp = _columnas_tabla(cur, 'productos_proveedores')
    columnas_p = _columnas_tabla(cur, 'productos')

    precio_cols = [
        c for c in ['precio_vigente', 'precio_ultimo', 'precio_medio', 'precio_compra', 'precio']
        if c in columnas_pp
    ]

    def _precio_actual_producto_validacion(producto_id, proveedor_id):
        if not producto_id or not precio_cols:
            return None
        producto_validado = _producto_id_en_ambito(conn, empresa_id, producto_id, centro_id)
        if not producto_validado:
            return None

        select_precio = "COALESCE(" + ", ".join(precio_cols) + ") AS precio_actual"
        sql = f"""
            SELECT {select_precio}
            FROM productos_proveedores
            WHERE producto_id = ?
        """
        params = [producto_validado]

        if 'proveedor_id' in columnas_pp and proveedor_id:
            sql += " AND (proveedor_id = ? OR proveedor_id IS NULL)"
            params.append(proveedor_id)

        if 'activo' in columnas_pp:
            sql += " AND COALESCE(activo, 1) = 1"

        orden = []
        if 'proveedor_id' in columnas_pp and proveedor_id:
            orden.append("CASE WHEN proveedor_id = ? THEN 0 ELSE 1 END")
            params.append(proveedor_id)
        if 'es_principal' in columnas_pp:
            orden.append("COALESCE(es_principal, 0) DESC")
        if 'preferente' in columnas_pp:
            orden.append("COALESCE(preferente, 0) DESC")
        orden.append("id DESC")
        sql += " ORDER BY " + ", ".join(orden) + " LIMIT 1"

        row = conn.execute(sql, tuple(params)).fetchone()
        if row and row['precio_actual'] is not None:
            return row['precio_actual']
        return None

    documento = conn.execute("""
        SELECT *
        FROM documentos_compra
        WHERE empresa_id = ?
          AND (recepcion_id = ? OR id = COALESCE(?, 0))
        ORDER BY id DESC
        LIMIT 1
    """, (empresa_id, id, recepcion['documento_adjunto_id'] if 'documento_adjunto_id' in recepcion.keys() else None)).fetchone()

    lineas_con_precio = []

    # 1) Si el documento tiene líneas OCR guardadas, se usan como base económica.
    if documento:
        precios_reparse_por_producto = {}
        try:
            texto_doc = documento['texto_extraido'] or ''
            if texto_doc:
                for linea_reparse in detectar_lineas_documento_compra(texto_doc):
                    precio_reparse = _to_float_safe(linea_reparse.get('precio'), None)
                    if not precio_reparse or precio_reparse <= 0:
                        continue
                    producto_reparse = buscar_producto_por_texto(
                        conn,
                        linea_reparse.get('descripcion') or '',
                        empresa_id,
                        referencia=linea_reparse.get('referencia') or '',
                        proveedor_id=recepcion['proveedor_id'],
                        centro_id=centro_id,
                    )
                    if producto_reparse and producto_reparse not in precios_reparse_por_producto:
                        precios_reparse_por_producto[producto_reparse] = precio_reparse
        except Exception:
            precios_reparse_por_producto = {}

        lineas_doc = conn.execute("""
            SELECT l.*,
                   p.nombre AS producto_nombre,
                   p.codigo AS producto_codigo
            FROM documento_compra_lineas_detectadas l
            LEFT JOIN productos p ON p.id = l.producto_id
            WHERE l.documento_id = ?
            ORDER BY l.id
        """, (documento['id'],)).fetchall()

        for l in lineas_doc:
            producto_id = l['producto_id']
            precio_actual = _precio_actual_producto_validacion(producto_id, recepcion['proveedor_id'])
            precio_documento = _to_float_safe(l['precio_detectado'], None)
            if (precio_documento is None or precio_documento <= 0) and producto_id in precios_reparse_por_producto:
                precio_documento = precios_reparse_por_producto.get(producto_id)

            lineas_con_precio.append({
                "id": f"doc_{l['id']}",
                "producto_id": producto_id,
                "producto_nombre": l['producto_nombre'] or l['descripcion_detectada'],
                "producto_codigo": l['producto_codigo'],
                "descripcion_detectada": l['descripcion_detectada'],
                "cantidad_detectada": l['cantidad_detectada'],
                "cantidad": l['cantidad_detectada'],
                "precio_actual": precio_actual,
                "precio_pedido": None,
                "precio_documento": precio_documento,
                "precio_detectado": precio_documento,
                "diferencia": (precio_documento - precio_actual) if precio_documento is not None and precio_actual is not None else None,
                "estado": l['estado'],
                "observaciones": l['observaciones'] if 'observaciones' in l.keys() else '',
                "origen": "documento"
            })

    # 2) Si no hay líneas OCR económicas, usamos SIEMPRE lineas_recepcion.
    # Esto evita que una recepción directa o una recepción con OCR temporal quede sin validar.
    if not lineas_con_precio:
        codigo_expr = "p.codigo AS producto_codigo" if 'codigo' in columnas_p else "'' AS producto_codigo"

        lineas_recepcion = conn.execute(f"""
            SELECT lr.*,
                   p.nombre AS producto_nombre,
                   {codigo_expr},
                   lp.precio_unitario AS precio_pedido
            FROM lineas_recepcion lr
            JOIN productos p ON p.id = lr.producto_id
            LEFT JOIN lineas_pedido lp
                   ON lp.pedido_id = ?
                  AND lp.producto_id = lr.producto_id
            WHERE lr.recepcion_id = ?
              AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
            ORDER BY lr.id
        """, (recepcion['pedido_id'], id, empresa_id)).fetchall()

        for l in lineas_recepcion:
            producto_id = l['producto_id']
            precio_actual = _precio_actual_producto_validacion(producto_id, recepcion['proveedor_id'])
            precio_pedido = l['precio_pedido'] if 'precio_pedido' in l.keys() else None
            precio_documento = l['precio_unitario'] if 'precio_unitario' in l.keys() else None

            lineas_con_precio.append({
                "id": f"lr_{l['id']}",
                "producto_id": producto_id,
                "producto_nombre": l['producto_nombre'],
                "producto_codigo": l['producto_codigo'],
                "descripcion_detectada": l['producto_nombre'],
                "cantidad_detectada": l['cantidad_recibida'],
                "cantidad": l['cantidad_recibida'],
                "precio_actual": precio_actual,
                "precio_pedido": precio_pedido,
                "precio_documento": precio_documento,
                "precio_detectado": precio_documento,
                "diferencia": (precio_documento - precio_actual) if precio_documento is not None and precio_actual is not None else None,
                "estado": "pendiente_precio" if precio_documento is None else "pendiente_validacion",
                "observaciones": "Línea procedente de recepción operativa; precio pendiente de compras." if precio_documento is None else "",
                "origen": "recepcion"
            })

    conn.close()

    return render_template(
        'compras/validacion_economica.html',
        recepcion=recepcion,
        documento=documento,
        lineas=lineas_con_precio
    )


@compras_bp.route('/documentos-compra/<int:documento_id>/ver')
@login_required
def ver_documento_compra(documento_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    documento = conn.execute("""
        SELECT dc.*
        FROM documentos_compra dc
        LEFT JOIN proveedores pr ON dc.proveedor_id = pr.id
        LEFT JOIN pedidos ped ON dc.pedido_id = ped.id
        LEFT JOIN recepciones r ON dc.recepcion_id = r.id
        WHERE dc.id = ?
          AND COALESCE(dc.empresa_id, ped.empresa_id, r.empresa_id, pr.empresa_id, ?) = ?
    """, (documento_id, empresa_id, empresa_id)).fetchone()

    conn.close()

    if not documento:
        flash('Documento no encontrado.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    ruta_archivo = documento['ruta_archivo']
    upload_dir = os.path.realpath(os.path.join(current_app.instance_path, 'uploads', 'compras'))
    ruta_real = os.path.realpath(ruta_archivo) if ruta_archivo else ''

    if not ruta_real.startswith(upload_dir + os.sep) or not os.path.exists(ruta_real):
        flash('El archivo no existe en el servidor.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    return send_file(
        ruta_real,
        mimetype=documento['mime_type'] or None,
        as_attachment=False,
        download_name=documento['nombre_archivo']
    )
    

def detectar_proveedor_desde_texto(conn, texto, empresa_id):
    if not texto:
        return None

    texto_lower = texto.lower()

    # 1️⃣ Buscar coincidencia con proveedores existentes
    proveedores = conn.execute("""
        SELECT id, nombre
        FROM proveedores
        WHERE empresa_id = ?
    """, (empresa_id,)).fetchall()

    for p in proveedores:
        nombre = (p['nombre'] or '').strip().lower()

        if not nombre or nombre == 'pendiente ia':
            continue

        if nombre in texto_lower:
            return p['id']

        palabras = [
            w for w in re.findall(r'[a-záéíóúüñ0-9]+', nombre)
            if len(w) >= 4
        ]

        if palabras and sum(1 for w in palabras if w in texto_lower) >= min(2, len(palabras)):
            return p['id']

    # 2️⃣ Detectar proveedor REAL del ticket (solo cabecera)
    candidatos = []

    for linea in texto.splitlines()[:12]:
        limpia = " ".join(linea.strip().split())
        lower = limpia.lower()

        if len(limpia) < 6:
            continue

        if any(x in lower for x in [
            'factura', 'fecha', 'cajero', 'ticket', 'total',
            'tarjeta', 'entregado', 'cambio'
        ]):
            continue

        if re.search(r'\d{5,}', limpia):
            continue

        if any(x in lower for x in [
            's.l', 's.l.', 'sl', 's.a', 'sa',
            'supermercado', 'hipermercado',
            'eurospar', 'mercado'
        ]):
            candidatos.append(limpia)

    if not candidatos:
        return None

    nombre = candidatos[0]
    nombre = re.sub(r'[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .,&/-]', ' ', nombre)
    nombre = " ".join(nombre.split())[:100]

    existente = conn.execute("""
        SELECT id
        FROM proveedores
        WHERE empresa_id = ?
        AND LOWER(nombre) = LOWER(?)
        LIMIT 1
    """, (empresa_id, nombre)).fetchone()

    if existente:
        return existente['id']

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO proveedores (nombre, activo, empresa_id)
        VALUES (?, 1, ?)
    """, (nombre, empresa_id))

    return cur.lastrowid

def obtener_o_crear_producto_desde_linea(cursor, descripcion, empresa_id, referencia=None):
    descripcion = (descripcion or '').strip()
    referencia = (referencia or '').strip()

    if len(descripcion) < 3:
        return None

    if referencia:
        existente_ref = cursor.execute("""
            SELECT id
            FROM productos
            WHERE empresa_id = ?
              AND (
                    codigo = ?
                    OR codigo_interno = ?
                  )
            LIMIT 1
        """, (empresa_id, referencia, referencia)).fetchone()

        if existente_ref:
            return existente_ref['id']

    palabras = [
        p.lower()
        for p in re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+', descripcion)
        if len(p) >= 3
    ]

    if palabras:
        productos = cursor.execute("""
            SELECT id, nombre
            FROM productos
            WHERE empresa_id = ?
              AND activo = 1
        """, (empresa_id,)).fetchall()

        mejor_id = None
        mejor_score = 0

        for p in productos:
            nombre = (p['nombre'] or '').lower()
            score = sum(1 for palabra in palabras if palabra in nombre)

            if score > mejor_score:
                mejor_score = score
                mejor_id = p['id']

        if mejor_score >= 2:
            return mejor_id

    cursor.execute("""
        INSERT INTO productos (
            nombre,
            codigo,
            activo,
            empresa_id,
            visible_en_compras,
            visible_en_produccion,
            visible_en_venta,
            permite_compra_directa
        )
        VALUES (?, ?, 1, ?, 1, 0, 0, 1)
    """, (
        descripcion,
        referencia or None,
        empresa_id
    ))

    return cursor.lastrowid


@compras_bp.route('/documentos-compra/<int:documento_id>/procesar', methods=['POST'])
@login_required
def procesar_documento_compra(documento_id):
    conn = get_db()
    cursor = conn.cursor()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_compras(conn, destino='compras.validar_recepciones')
    if bloqueo:
        return bloqueo

    try:
        documento = conn.execute("""
            SELECT *
            FROM documentos_compra
            WHERE id = ? AND empresa_id = ?
        """, (documento_id, empresa_id)).fetchone()

        if not documento:
            conn.close()
            flash('Documento no encontrado.', 'danger')
            return redirect(url_for('compras.validar_recepciones'))
        centro_id = _centro_id_desde_documento_compra(conn, documento_id, empresa_id)
        centro_activo = _centro_activo_id()
        if centro_activo and centro_id and int(centro_id) != int(centro_activo):
            conn.close()
            flash('El documento no pertenece al centro activo.', 'warning')
            return redirect(url_for('compras.validar_recepciones'))

        texto_extraido = extraer_texto_documento_compra(documento['ruta_archivo'])

        proveedor_id = detectar_proveedor_desde_texto(conn, texto_extraido, empresa_id)

        if proveedor_id:
            cursor.execute("""
                UPDATE documentos_compra
                SET proveedor_id = ?
                WHERE id = ? AND empresa_id = ?
            """, (proveedor_id, documento_id, empresa_id))

            if documento['recepcion_id']:
                cursor.execute("""
                    UPDATE recepciones
                    SET proveedor_id = ?
                    WHERE id = ? AND empresa_id = ?
                """, (proveedor_id, documento['recepcion_id'], empresa_id))

        cursor.execute("""
            UPDATE documentos_compra
            SET texto_extraido = ?,
                procesado_ia = 1
            WHERE id = ? AND empresa_id = ?
        """, (texto_extraido, documento_id, empresa_id))

        lineas = detectar_lineas_documento_compra(texto_extraido)

        cursor.execute("""
            DELETE FROM documento_compra_lineas_detectadas
            WHERE documento_id = ?
        """, (documento_id,))

        detectadas = 0
        asociadas = 0
        pendientes = 0

        for linea in lineas:
            referencia = linea.get('referencia') or ''
            descripcion = linea.get('descripcion') or ''
            cantidad = linea.get('cantidad') or 0
            precio = linea.get('precio')
            precio_origen = linea.get('precio_origen') or 'ocr'

            producto_id = buscar_producto_por_texto(
                conn,
                descripcion,
                empresa_id,
                referencia=referencia,
                proveedor_id=proveedor_id or documento['proveedor_id'],
                centro_id=centro_id,
            )

            estado = 'pendiente_producto'

            if producto_id:
                asociadas += 1
                estado = 'detectado'
            else:
                pendientes += 1

            cursor.execute("""
                INSERT INTO documento_compra_lineas_detectadas (
                    documento_id,
                    producto_id,
                    descripcion_detectada,
                    cantidad_detectada,
                    precio_detectado,
                    estado,
                    observaciones
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                documento_id,
                producto_id,
                descripcion,
                cantidad,
                precio,
                estado,
                f"Referencia: {referencia or '-'} | Precio origen: {precio_origen}"
            ))

            detectadas += 1

        conn.commit()

        flash(
            f'Documento procesado: {detectadas} líneas detectadas, '
            f'{asociadas} asociadas y {pendientes} pendientes de asociar.',
            'success'
        )

    except Exception as e:
        conn.rollback()
        flash(f'Error procesando documento: {str(e)}', 'danger')

    finally:
        conn.close()

    return redirect(url_for('compras.validar_recepciones'))

@compras_bp.route('/documentos-compra/<int:documento_id>/detalle')
@login_required
def detalle_documento_compra(documento_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    documento = conn.execute("""
        SELECT *
        FROM documentos_compra
        WHERE id = ? AND empresa_id = ?
    """, (documento_id, empresa_id)).fetchone()

    conn.close()

    if not documento:
        flash('Documento no encontrado.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    return render_template(
        'compras/detalle_documento_compra.html',
        documento=documento
    ) 
    
@compras_bp.route('/documentos-compra/<int:documento_id>/detectar-lineas', methods=['POST'])
@login_required
def detectar_lineas_documento(documento_id):
    conn = get_db()
    cursor = conn.cursor()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_compras(conn, destino='compras.validar_recepciones')
    if bloqueo:
        return bloqueo

    documento = conn.execute("""
        SELECT *
        FROM documentos_compra
        WHERE id = ? AND empresa_id = ?
    """, (documento_id, empresa_id)).fetchone()

    if not documento:
        conn.close()
        flash('Documento no encontrado.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))
    centro_id = _centro_id_desde_documento_compra(conn, documento_id, empresa_id)
    centro_activo = _centro_activo_id()
    if centro_activo and centro_id and int(centro_id) != int(centro_activo):
        conn.close()
        flash('El documento no pertenece al centro activo.', 'warning')
        return redirect(url_for('compras.validar_recepciones'))

    texto = documento['texto_extraido'] or ''

    if not texto:
        conn.close()
        flash('El documento no tiene texto extraído. Procesa primero el documento.', 'warning')
        return redirect(url_for('compras.detalle_documento_compra', documento_id=documento_id))

    cursor.execute("""
        DELETE FROM documento_compra_lineas_detectadas
        WHERE documento_id = ?
    """, (documento_id,))

    lineas = detectar_lineas_documento_compra(texto)

    for linea in lineas:
        cursor.execute("""
            INSERT INTO documento_compra_lineas_detectadas (
                documento_id,
                descripcion_detectada,
                cantidad_detectada,
                precio_detectado,
                estado
            )
            VALUES (?, ?, ?, ?, 'pendiente')
        """, (
            documento_id,
            linea['descripcion'],
            linea['cantidad'],
            linea['precio']
        ))

    conn.commit()
    conn.close()

    flash(f'Se han detectado {len(lineas)} posibles líneas.', 'success')
    return redirect(url_for('compras.revisar_lineas_detectadas', documento_id=documento_id))

@compras_bp.route('/documentos-compra/<int:documento_id>/lineas-detectadas')
@login_required
def revisar_lineas_detectadas(documento_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    documento = conn.execute("""
        SELECT *
        FROM documentos_compra
        WHERE id = ? AND empresa_id = ?
    """, (documento_id, empresa_id)).fetchone()
    centro_id = _centro_id_desde_documento_compra(conn, documento_id, empresa_id) if documento else None
    producto_scope_sql, producto_scope_params = _centro_sql_columna(conn, "productos", "productos", centro_id, permite_global=True)

    lineas = conn.execute("""
        SELECT *
        FROM documento_compra_lineas_detectadas
        WHERE documento_id = ?
        ORDER BY id
    """, (documento_id,)).fetchall()

    productos = conn.execute("""
        SELECT id, nombre, codigo_interno
        FROM productos
        WHERE activo = 1
          AND empresa_id = ?
          """ + producto_scope_sql + """
        ORDER BY nombre
    """, [empresa_id] + producto_scope_params).fetchall()

    conn.close()

    if not documento:
        flash('Documento no encontrado.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    return render_template(
        'compras/lineas_detectadas_documento.html',
        documento=documento,
        lineas=lineas,
        productos=productos
    )


@compras_bp.route('/documentos-compra/<int:documento_id>/lineas-detectadas/asociar', methods=['POST'])
@login_required
def asociar_lineas_detectadas(documento_id):
    conn = get_db()
    cur = conn.cursor()
    empresa_id = session.get('user_empresa_id', 1)
    bloqueo = _requiere_gestion_compras(conn, destino='compras.validar_recepciones')
    if bloqueo:
        return bloqueo
    _asegurar_estructura_compras(cur)

    documento = conn.execute("""
        SELECT *
        FROM documentos_compra
        WHERE id = ? AND empresa_id = ?
    """, (documento_id, empresa_id)).fetchone()

    if not documento:
        conn.close()
        flash('Documento no encontrado.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))
    centro_id = _centro_id_desde_documento_compra(conn, documento_id, empresa_id)
    centro_activo = _centro_activo_id()
    if centro_activo and centro_id and int(centro_id) != int(centro_activo):
        conn.close()
        flash('El documento no pertenece al centro activo.', 'warning')
        return redirect(url_for('compras.validar_recepciones'))

    proveedor_id = documento['proveedor_id']
    if not proveedor_id and documento['recepcion_id']:
        recepcion = conn.execute("SELECT proveedor_id FROM recepciones WHERE id = ? AND empresa_id = ?", (documento['recepcion_id'], empresa_id)).fetchone()
        proveedor_id = recepcion['proveedor_id'] if recepcion else None

    lineas_ids = request.form.getlist('linea_id[]')
    asociadas = 0

    try:
        for linea_id in lineas_ids:
            producto_id = _to_int_or_none(request.form.get(f'producto_id_{linea_id}'))
            if not producto_id:
                continue
            producto_id = _producto_id_en_ambito(conn, empresa_id, producto_id, centro_id)
            if not producto_id:
                continue

            linea = conn.execute("""
                SELECT *
                FROM documento_compra_lineas_detectadas
                WHERE id = ? AND documento_id = ?
            """, (linea_id, documento_id)).fetchone()
            if not linea:
                continue

            descripcion = linea['descripcion_detectada'] or ''
            referencia = ''
            obs = linea['observaciones'] or ''
            match_ref = re.search(r'(?:Ref(?:erencia)?|Referencia)\s*:\s*([^|]+)', obs, flags=re.I)
            if match_ref:
                referencia = match_ref.group(1).strip()
                if referencia == '-':
                    referencia = ''

            cur.execute("""
                UPDATE documento_compra_lineas_detectadas
                SET producto_id = ?,
                    estado = 'asociado_manual',
                    observaciones = COALESCE(observaciones, '') || ' | Asociado manualmente'
                WHERE id = ? AND documento_id = ?
            """, (producto_id, linea_id, documento_id))

            _guardar_alias_producto_compra(
                cur,
                empresa_id,
                proveedor_id,
                producto_id,
                descripcion,
                referencia=referencia,
            )
            asociadas += 1

        conn.commit()
        flash(f'Asociaciones guardadas: {asociadas}. El OCR usara estas equivalencias en proximos documentos.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error guardando asociaciones OCR: {str(e)}', 'danger')
    finally:
        conn.close()

    return redirect(url_for('compras.revisar_lineas_detectadas', documento_id=documento_id))
    
def crear_proveedor_desde_texto(cursor, texto, empresa_id):
    """
    Función conservadora: solo crea proveedor si el nombre es muy claro.
    Si no lo ve claro, devuelve None para mantener PENDIENTE IA.
    """
    if not texto:
        return None

    palabras_prohibidas = [
        'error_', 'pdf_', 'ocr_', 'no module', 'tesseract',
        'sin_texto', 'extraccion', 'extraction', 'fecha', 'cajero',
        'factura', 'ticket', 'total', 'tarjeta', 'entregado', 'cambio',
        'cliente', 'clientes', 'documento', 'artículo', 'articulo',
        'cantidad', 'precio', 'importe', 'base', 'igic', 'iva',
        'pedido', 'terminal', 'autorizacion', 'autorización'
    ]

    indicios_empresa = [
        's.l', 's.l.', 'sl', 's.a', 's.a.', 'sa',
        'supermercado', 'hipermercado', 'ferreteria', 'ferretería'
    ]

    for linea in texto.splitlines()[:10]:
        limpia = " ".join((linea or '').strip().split())
        lower = limpia.lower()

        if len(limpia) < 6:
            continue
        if any(p in lower for p in palabras_prohibidas):
            continue
        if not any(x in lower for x in indicios_empresa):
            continue
        if re.search(r'\b\d{5,}\b', limpia):
            continue

        letras = re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', limpia)
        if len(letras) < 6:
            continue

        nombre = re.sub(r'[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .,&/-]', ' ', limpia)
        nombre = " ".join(nombre.split())[:100]

        existente = cursor.execute("""
            SELECT id
            FROM proveedores
            WHERE empresa_id = ?
              AND LOWER(nombre) = LOWER(?)
            LIMIT 1
        """, (empresa_id, nombre)).fetchone()

        if existente:
            return existente['id']

        cursor.execute("""
            INSERT INTO proveedores (
                nombre,
                activo,
                empresa_id
            )
            VALUES (?, 1, ?)
        """, (nombre, empresa_id))
        return cursor.lastrowid

    return None

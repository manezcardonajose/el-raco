# -*- coding: utf-8 -*-
import os
import sqlite3
from flask import session
from logger_config import logger, log_exception
from helpers.db_safe import column_exists, safe_add_column, validate_table_name

DATABASE = "pepe4.db"


def get_database_path():
    """Devuelve la base activa; permite lanzar demos sin tocar la base real."""
    return os.environ.get("ERP_DATABASE") or DATABASE


def get_db():
    """Obtiene una conexión SQLite configurada para la app."""
    try:
        conn = sqlite3.connect(
            get_database_path(),
            timeout=int(os.environ.get("DB_TIMEOUT", 10)),
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn
    except Exception as exc:
        log_exception(exc, "Conectando a BD")
        raise


def get_empresa_id():
    """Devuelve el ID de empresa del usuario actual."""
    return session.get("user_empresa_id", 1)


def _column_exists(cursor, table_name, column_name):
    """Wrapper para mantener compatibilidad con código antiguo."""
    return column_exists(cursor, table_name, column_name)


def _safe_add_column(cursor, table_name, column_def):
    """
    Wrapper para mantener compatibilidad con código antiguo.
    column_def ejemplo: 'empresa_id INTEGER DEFAULT 1'
    """
    try:
        parts = column_def.split()
        column_name = parts[0]
        column_type = parts[1] if len(parts) > 1 else "TEXT"
        default = " ".join(parts[2:]) if len(parts) > 2 else None
        
        safe_add_column(cursor, table_name, column_name, column_type, default)
    except Exception as exc:
        log_exception(exc, f"Agregando columna a {table_name}")


def init_db():
    """Inicializa la base de datos sin bloquear ni duplicar datos."""
    conn = get_db()
    cursor = conn.cursor()

    try:
        # ============================================
        # TABLAS PRINCIPALES
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS empresas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            nif TEXT,
            direccion TEXT,
            telefono TEXT,
            email TEXT,
            logo TEXT,
            activo BOOLEAN DEFAULT 1
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS proveedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            nif TEXT,
            email TEXT,
            telefono TEXT,
            direccion TEXT,
            contacto TEXT,
            observaciones TEXT,
            activo BOOLEAN DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            cantidad REAL DEFAULT 0,
            unidad TEXT,
            tipo_conservacion TEXT DEFAULT 'seco',
            familia TEXT DEFAULT 'otros',
            precio REAL DEFAULT 0,
            stock_minimo REAL DEFAULT 0,
            codigo TEXT,
            referencia_proveedor TEXT,
            ubicacion TEXT,
            alergenos TEXT,
            alergenos_validado INTEGER DEFAULT 0,
            observaciones TEXT,
            activo BOOLEAN DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            proveedor_id INTEGER,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id),
            FOREIGN KEY (proveedor_id) REFERENCES proveedores(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS productos_proveedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            proveedor_id INTEGER NOT NULL,
            precio REAL DEFAULT 0,
            es_principal BOOLEAN DEFAULT 0,
            fecha_actualizacion DATE DEFAULT CURRENT_DATE,
            FOREIGN KEY (producto_id) REFERENCES productos(id),
            FOREIGN KEY (proveedor_id) REFERENCES proveedores(id),
            UNIQUE(producto_id, proveedor_id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS historico_precios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            proveedor_id INTEGER NOT NULL,
            fecha DATE DEFAULT CURRENT_DATE,
            precio REAL NOT NULL,
            motivo TEXT,
            pedido_id INTEGER,
            FOREIGN KEY (producto_id) REFERENCES productos(id),
            FOREIGN KEY (proveedor_id) REFERENCES proveedores(id)
        );
        """)

        # ============================================
        # AUTENTICACIÓN Y PERMISOS
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS permisos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE,
            descripcion TEXT
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS roles_permisos (
            rol_id INTEGER NOT NULL,
            permiso_id INTEGER NOT NULL,
            PRIMARY KEY (rol_id, permiso_id),
            FOREIGN KEY (rol_id) REFERENCES roles(id),
            FOREIGN KEY (permiso_id) REFERENCES permisos(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            rol_id INTEGER NOT NULL,
            empresa_id INTEGER DEFAULT 1,
            activo BOOLEAN DEFAULT 1,
            ultimo_acceso DATETIME,
            empleado_id INTEGER,
            codigo_acceso TEXT,
            debe_cambiar_password INTEGER DEFAULT 0,
            es_global INTEGER DEFAULT 0,
            puede_ver_precios INTEGER DEFAULT 0,
            puede_modificar_precios INTEGER DEFAULT 0,
            puede_validar_recepcion INTEGER DEFAULT 0,
            puede_validar_economicamente INTEGER DEFAULT 0,
            fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (empleado_id) REFERENCES empleados(id),
            FOREIGN KEY (rol_id) REFERENCES roles(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER,
            accion TEXT NOT NULL,
            tabla TEXT,
            registro_id INTEGER,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            ip TEXT,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        );
        """)

        # ============================================
        # DEPARTAMENTOS / SUBDEPARTAMENTOS
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS departamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            activo BOOLEAN DEFAULT 1,
            es_base BOOLEAN DEFAULT 0, -- Para marcar departamentos como 'Cocina', 'Pisos'
            centro_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS subdepartamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            departamento_id INTEGER NOT NULL,
            descripcion TEXT,
            activo BOOLEAN DEFAULT 1,
            centro_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (departamento_id) REFERENCES departamentos(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        # ============================================
        # RRHH (NUEVO MÓDULO ESTRUCTURAL)
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS categorias_laborales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            empresa_id INTEGER DEFAULT 1
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS empleados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            apellidos TEXT,
            nif TEXT,
            email TEXT,
            telefono TEXT,
            direccion TEXT,
            fecha_alta DATE DEFAULT CURRENT_DATE,
            fecha_baja DATE,
            categoria_id INTEGER,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            activo BOOLEAN DEFAULT 1,
            centro_id INTEGER,
            tipo_empleado TEXT DEFAULT 'real',
            origen_importacion TEXT,
            codigo_importacion TEXT,
            motivo_cambio TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (categoria_id) REFERENCES categorias_laborales(id),
            FOREIGN KEY (departamento_id) REFERENCES departamentos(id),
            FOREIGN KEY (subdepartamento_id) REFERENCES subdepartamentos(id)
        );
        """)
        for col in [
            "punto_venta_id INTEGER",
            "rol TEXT",
            "usuario_id INTEGER",
            "observaciones TEXT",
        ]:
            _safe_add_column(cursor, "empleados", col)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS fichajes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            tipo TEXT NOT NULL, -- 'entrada', 'salida'
            fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP,
            latitud REAL,
            longitud REAL,
            dispositivo TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (empleado_id) REFERENCES empleados(id)
        );
        """)

        # ============================================
        # ALMACENES Y STOCK
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS almacenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo TEXT DEFAULT 'seco',
            ubicacion TEXT,
            responsable TEXT,
            departamento_id INTEGER NOT NULL, -- Obligatorio por Plan Maestro
            subdepartamento_id INTEGER,
            equipo_mantenimiento_id INTEGER,
            equipo_appcc_id INTEGER,
            requiere_control_temperatura INTEGER DEFAULT 0,
            rango_min REAL,
            rango_max REAL,
            centro_id INTEGER,
            activo BOOLEAN DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (departamento_id) REFERENCES departamentos(id),
            FOREIGN KEY (subdepartamento_id) REFERENCES subdepartamentos(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_almacen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            almacen_id INTEGER NOT NULL,
            cantidad REAL DEFAULT 0,
            stock_minimo REAL DEFAULT 0,
            FOREIGN KEY (producto_id) REFERENCES productos(id),
            FOREIGN KEY (almacen_id) REFERENCES almacenes(id),
            UNIQUE(producto_id, almacen_id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS movimientos_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            almacen_id INTEGER NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            tipo TEXT NOT NULL,
            cantidad REAL NOT NULL,
            motivo TEXT,
            usuario TEXT,
            documento TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (producto_id) REFERENCES productos(id),
            FOREIGN KEY (almacen_id) REFERENCES almacenes(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS traspasos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            origen_almacen_id INTEGER NOT NULL,
            destino_almacen_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad REAL NOT NULL,
            usuario TEXT,
            estado TEXT DEFAULT 'completado',
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (origen_almacen_id) REFERENCES almacenes(id),
            FOREIGN KEY (destino_almacen_id) REFERENCES almacenes(id),
            FOREIGN KEY (producto_id) REFERENCES productos(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        # ============================================
        # RECETAS
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS recetas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            alergenos TEXT,
            imagen TEXT,
            tiempo_preparacion INTEGER,
            dificultad TEXT,
            categoria TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS recetas_ingredientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receta_id INTEGER,
            producto_id INTEGER,
            cantidad REAL,
            FOREIGN KEY (receta_id) REFERENCES recetas(id) ON DELETE CASCADE,
            FOREIGN KEY (producto_id) REFERENCES productos(id)
        );
        """)

        # ============================================
        # COMPRAS
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_pedido TEXT UNIQUE,
            proveedor_id INTEGER NOT NULL,
            fecha_pedido DATE DEFAULT CURRENT_DATE,
            fecha_entrega DATE,
            estado TEXT DEFAULT 'borrador',
            observaciones TEXT,
            creado_por TEXT,
            solicitado_por TEXT,
            fecha_solicitud_autorizacion DATETIME,
            aprobado_por TEXT,
            fecha_aprobacion DATE,
            rechazado_por TEXT,
            fecha_rechazo DATETIME,
            motivo_rechazo TEXT,
            motivo_cancelacion TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (proveedor_id) REFERENCES proveedores(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS lineas_pedido (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad_solicitada REAL NOT NULL,
            cantidad_recibida REAL DEFAULT 0,
            precio_unitario REAL NOT NULL,
            FOREIGN KEY (pedido_id) REFERENCES pedidos(id) ON DELETE CASCADE,
            FOREIGN KEY (producto_id) REFERENCES productos(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS recepciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER,
            proveedor_id INTEGER NOT NULL,
            fecha_recepcion DATE DEFAULT CURRENT_DATE,
            numero_albaran TEXT,
            observaciones TEXT,
            recibido_por TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (pedido_id) REFERENCES pedidos(id),
            FOREIGN KEY (proveedor_id) REFERENCES proveedores(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS lineas_recepcion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recepcion_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad_recibida REAL NOT NULL,
            lote TEXT,
            fecha_caducidad DATE,
            temperatura REAL,
            almacen_id INTEGER,
            precio_unitario REAL,
            importe_total REAL,
            num_envases INTEGER DEFAULT 1,
            lote_id INTEGER,
            aceptado BOOLEAN DEFAULT 1,
            motivo_rechazo TEXT,
            incidencia_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (recepcion_id) REFERENCES recepciones(id) ON DELETE CASCADE,
            FOREIGN KEY (producto_id) REFERENCES productos(id),
            FOREIGN KEY (almacen_id) REFERENCES almacenes(id)
        );
        """)

        # ============================================
        # LOTES Y TRAZABILIDAD
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS lotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo_lote TEXT NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad_inicial REAL DEFAULT 0,
            cantidad_actual REAL DEFAULT 0,
            fecha_recepcion DATE DEFAULT CURRENT_DATE,
            fecha_caducidad DATE,
            proveedor_id INTEGER,
            almacen_id INTEGER,
            temperatura_recepcion REAL,
            lote_proveedor TEXT,
            origen_lote TEXT DEFAULT 'proveedor',
            albaran TEXT,
            estado TEXT DEFAULT 'activo',
            alerta_enviada BOOLEAN DEFAULT 0,
            observaciones TEXT,
            responsable_recepcion TEXT,
            fecha_descongelado DATETIME,
            temperatura_descongelado REAL,
            responsable_descongelado TEXT,
            observaciones_descongelado TEXT,
            estado_descongelado TEXT DEFAULT 'pendiente',
            ubicacion_actual TEXT,
            fecha_ultimo_control DATETIME,
            incidencia_id INTEGER,
            fecha_consumo DATETIME,
            parte_consumo_id INTEGER,
            documento_origen TEXT,
            notas_trazabilidad TEXT,
            coste_unitario REAL DEFAULT 0,
            coste_total REAL DEFAULT 0,
            moneda TEXT DEFAULT 'EUR',
            fecha_deterioro DATETIME,
            motivo_deterioro TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (producto_id) REFERENCES productos(id),
            FOREIGN KEY (proveedor_id) REFERENCES proveedores(id),
            FOREIGN KEY (almacen_id) REFERENCES almacenes(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

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
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS registros_descongelado (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha_control DATETIME DEFAULT CURRENT_TIMESTAMP,
            temperatura REAL NOT NULL,
            aspecto TEXT,
            olfativo TEXT,
            decision TEXT,
            responsable TEXT NOT NULL,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (lote_id) REFERENCES lotes(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS composicion_lotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_producto_id INTEGER NOT NULL,
            lote_ingrediente_id INTEGER NOT NULL,
            cantidad_usada REAL NOT NULL,
            FOREIGN KEY (lote_producto_id) REFERENCES lotes(id),
            FOREIGN KEY (lote_ingrediente_id) REFERENCES lotes(id)
        );
        """)

        # ============================================
        # PUNTOS DE VENTA / MENÚS
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS puntos_venta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo TEXT,
            almacen_id INTEGER,
            centro_id INTEGER,
            activo BOOLEAN DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (almacen_id) REFERENCES almacenes(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS punto_venta_departamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            punto_venta_id INTEGER NOT NULL,
            departamento_id INTEGER NOT NULL,
            rol_operativo TEXT DEFAULT 'general',
            UNIQUE(punto_venta_id, departamento_id, rol_operativo)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS punto_venta_recetas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            punto_venta_id INTEGER NOT NULL,
            receta_id INTEGER NOT NULL,
            precio_venta REAL DEFAULT 0,
            activo INTEGER DEFAULT 1,
            UNIQUE(punto_venta_id, receta_id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tipos_menu (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            punto_venta_id INTEGER,
            activo BOOLEAN DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (punto_venta_id) REFERENCES puntos_venta(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS menus_diarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            tipo_menu_id INTEGER NOT NULL,
            departamento_id INTEGER, -- Añadido según Plan Maestro
            punto_venta_id INTEGER,  -- Añadido según Plan Maestro
            modo_servicio TEXT DEFAULT 'buffet', -- 'buffet', 'carta', 'menu_diario', 'mixto'
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (departamento_id) REFERENCES departamentos(id),
            FOREIGN KEY (punto_venta_id) REFERENCES puntos_venta(id),
            FOREIGN KEY (tipo_menu_id) REFERENCES tipos_menu(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS menu_subdepartamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_diario_id INTEGER NOT NULL,
            subdepartamento_id INTEGER NOT NULL,
            seccion TEXT,
            orden INTEGER DEFAULT 0,
            FOREIGN KEY (menu_diario_id) REFERENCES menus_diarios(id) ON DELETE CASCADE,
            FOREIGN KEY (subdepartamento_id) REFERENCES subdepartamentos(id) ON DELETE CASCADE
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS menu_platos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_subdepartamento_id INTEGER NOT NULL,
            receta_id INTEGER NOT NULL,
            orden INTEGER DEFAULT 0,
            FOREIGN KEY (menu_subdepartamento_id) REFERENCES menu_subdepartamentos(id) ON DELETE CASCADE,
            FOREIGN KEY (receta_id) REFERENCES recetas(id) ON DELETE CASCADE
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS menus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo_menu_id INTEGER NOT NULL,
            departamento_id INTEGER, -- Añadido según Plan Maestro
            punto_venta_id INTEGER,  -- Añadido según Plan Maestro
            modo_servicio TEXT DEFAULT 'buffet',
            fecha_inicio DATE,
            fecha_fin DATE,
            activo BOOLEAN DEFAULT 1,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (departamento_id) REFERENCES departamentos(id),
            FOREIGN KEY (punto_venta_id) REFERENCES puntos_venta(id),
            FOREIGN KEY (tipo_menu_id) REFERENCES tipos_menu(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS menu_fijo_subdepartamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id INTEGER NOT NULL,
            subdepartamento_id INTEGER,
            seccion TEXT,
            orden INTEGER DEFAULT 0,
            FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS menu_fijo_platos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_fijo_subdepartamento_id INTEGER NOT NULL,
            receta_id INTEGER NOT NULL,
            orden INTEGER DEFAULT 0,
            FOREIGN KEY (menu_fijo_subdepartamento_id)
                REFERENCES menu_fijo_subdepartamentos(id) ON DELETE CASCADE,
            FOREIGN KEY (receta_id) REFERENCES recetas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS lineas_menu (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id INTEGER NOT NULL,
            tipo_item TEXT DEFAULT 'receta',
            receta_id INTEGER,
            producto_id INTEGER,
            cantidad_estimada REAL,
            orden INTEGER DEFAULT 0,
            precio REAL,
            seccion TEXT,
            menu_subdepartamento_id INTEGER,
            FOREIGN KEY (menu_id) REFERENCES menus(id),
            FOREIGN KEY (receta_id) REFERENCES recetas(id),
            FOREIGN KEY (producto_id) REFERENCES productos(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS planificacion_semanal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            semana INTEGER NOT NULL,
            dia_semana INTEGER NOT NULL,
            menu_frio_id INTEGER,
            menu_caliente_id INTEGER,
            menu_postres_id INTEGER,
            menu_desayunos_id INTEGER,
            activo BOOLEAN DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (menu_frio_id) REFERENCES menus_diarios(id),
            FOREIGN KEY (menu_caliente_id) REFERENCES menus_diarios(id),
            FOREIGN KEY (menu_postres_id) REFERENCES menus_diarios(id),
            FOREIGN KEY (menu_desayunos_id) REFERENCES menus_diarios(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id),
            UNIQUE(semana, dia_semana, empresa_id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS planificacion_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            tipo_menu_id INTEGER NOT NULL,
            menu_diario_id INTEGER NOT NULL,
            subdepartamento_id INTEGER,
            orden INTEGER DEFAULT 0,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (tipo_menu_id) REFERENCES tipos_menu(id),
            FOREIGN KEY (menu_diario_id) REFERENCES menus_diarios(id),
            FOREIGN KEY (subdepartamento_id) REFERENCES subdepartamentos(id),
            UNIQUE(fecha, tipo_menu_id, menu_diario_id, subdepartamento_id)
        );
        """)

        # ============================================
        # PRODUCCIÓN
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS partes_produccion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            fecha_prevista DATE,
            hora TEXT,
            hora_inicio DATETIME,
            hora_fin DATETIME,
            responsable TEXT,
            turno TEXT,
            punto_venta_id INTEGER,
            observaciones TEXT,
            estado TEXT DEFAULT 'borrador',
            menu_diario_id INTEGER,
            notificado BOOLEAN DEFAULT 0,
            notificado_a TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (punto_venta_id) REFERENCES puntos_venta(id),
            FOREIGN KEY (menu_diario_id) REFERENCES menus_diarios(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS lineas_produccion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parte_id INTEGER NOT NULL,
            receta_id INTEGER NOT NULL,
            cantidad_planificada REAL,
            cantidad_real REAL,
            cantidad_producida REAL,
            coste_estimado REAL,
            coste_real REAL,
            coste_unitario_teorico REAL,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (parte_id) REFERENCES partes_produccion(id) ON DELETE CASCADE,
            FOREIGN KEY (receta_id) REFERENCES recetas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS consumo_produccion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            linea_produccion_id INTEGER NOT NULL,
            lote_id INTEGER NOT NULL,
            cantidad_consumida REAL NOT NULL,
            producto_id INTEGER,
            coste_unitario REAL DEFAULT 0,
            coste_total REAL DEFAULT 0,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (linea_produccion_id) REFERENCES lineas_produccion(id) ON DELETE CASCADE,
            FOREIGN KEY (lote_id) REFERENCES lotes(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS consumo_extra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parte_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad REAL NOT NULL,
            motivo TEXT,
            usuario TEXT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parte_id) REFERENCES partes_produccion(id) ON DELETE CASCADE,
            FOREIGN KEY (producto_id) REFERENCES productos(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingredientes_omitidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parte_id INTEGER NOT NULL,
            receta_ingrediente_id INTEGER NOT NULL,
            motivo TEXT,
            usuario TEXT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parte_id) REFERENCES partes_produccion(id) ON DELETE CASCADE,
            FOREIGN KEY (receta_ingrediente_id) REFERENCES recetas_ingredientes(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS mermas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            producto_id INTEGER NOT NULL,
            lote_id INTEGER,
            cantidad REAL NOT NULL,
            motivo TEXT NOT NULL,
            responsable TEXT,
            observaciones TEXT,
            destino TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (producto_id) REFERENCES productos(id),
            FOREIGN KEY (lote_id) REFERENCES lotes(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        # ============================================
        # MANTENIMIENTO
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS equipos_mantenimiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT,
            nombre TEXT NOT NULL,
            tipo TEXT NOT NULL,
            familia TEXT,
            categoria_operativa TEXT,
            clase_activo TEXT,
            criticidad TEXT DEFAULT 'media',
            inventariable INTEGER DEFAULT 1,
            requiere_appcc INTEGER DEFAULT 0,
            equipo_appcc_id INTEGER,
            origen_modulo TEXT,
            habitacion_id INTEGER,
            zona_id INTEGER,
            almacen_id INTEGER,
            marca TEXT,
            modelo TEXT,
            numero_serie TEXT,
            fecha_compra DATE,
            fecha_instalacion DATE,
            garantia_meses INTEGER,
            proveedor_id INTEGER,
            ubicacion TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            manual_pdf TEXT,
            foto TEXT,
            activo BOOLEAN DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (proveedor_id) REFERENCES proveedores(id),
            FOREIGN KEY (departamento_id) REFERENCES departamentos(id),
            FOREIGN KEY (subdepartamento_id) REFERENCES subdepartamentos(id),
            FOREIGN KEY (punto_venta_id) REFERENCES puntos_venta(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS mantenimiento_preventivo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equipo_id INTEGER NOT NULL,
            tarea TEXT NOT NULL,
            frecuencia TEXT NOT NULL,
            intervalo_dias INTEGER,
            responsable TEXT,
            instrucciones TEXT,
            ultima_ejecucion DATE,
            proxima_ejecucion DATE,
            activo BOOLEAN DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (equipo_id) REFERENCES equipos_mantenimiento(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS ordenes_trabajo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_orden TEXT UNIQUE NOT NULL,
            equipo_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            prioridad TEXT NOT NULL,
            estado TEXT NOT NULL,
            fecha_apertura DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_asignacion DATETIME,
            fecha_inicio DATETIME,
            fecha_fin DATETIME,
            descripcion TEXT NOT NULL,
            tecnico_asignado TEXT,
            horas_estimadas REAL,
            horas_reales REAL,
            coste_estimado REAL,
            coste_real REAL,
            observaciones TEXT,
            incidencia_id INTEGER,
            origen_modulo TEXT,
            origen_tipo TEXT,
            origen_id INTEGER,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            ubicacion TEXT,
            habitacion_id INTEGER,
            zona_id INTEGER,
            requiere_appcc INTEGER DEFAULT 0,
            persistente INTEGER DEFAULT 0,
            cerrado_por TEXT,
            fecha_cierre DATETIME,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (equipo_id) REFERENCES equipos_mantenimiento(id),
            FOREIGN KEY (incidencia_id) REFERENCES incidencias(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS repuestos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT UNIQUE NOT NULL,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            proveedor_id INTEGER,
            referencia_proveedor TEXT,
            ubicacion_almacen TEXT,
            stock_actual REAL DEFAULT 0,
            stock_minimo REAL DEFAULT 0,
            precio_compra REAL DEFAULT 0,
            precio_venta REAL DEFAULT 0,
            unidad TEXT DEFAULT 'ud',
            compatible_con TEXT,
            activo BOOLEAN DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (proveedor_id) REFERENCES proveedores(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS ordenes_repuestos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orden_id INTEGER NOT NULL,
            repuesto_id INTEGER NOT NULL,
            cantidad REAL NOT NULL,
            precio_unitario REAL,
            FOREIGN KEY (orden_id) REFERENCES ordenes_trabajo(id) ON DELETE CASCADE,
            FOREIGN KEY (repuesto_id) REFERENCES repuestos(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS historial_mantenimiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equipo_id INTEGER NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            tipo TEXT NOT NULL,
            orden_id INTEGER,
            descripcion TEXT NOT NULL,
            tecnico TEXT,
            horas REAL,
            coste REAL,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (equipo_id) REFERENCES equipos_mantenimiento(id),
            FOREIGN KEY (orden_id) REFERENCES ordenes_trabajo(id)
        );
        """)

        # ============================================
        # APPCC
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS equipos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo TEXT,
            ubicacion TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            codigo TEXT,
            equipo_mantenimiento_id INTEGER,
            almacen_id INTEGER,
            requiere_mantenimiento INTEGER DEFAULT 1,
            rango_min REAL,
            rango_max REAL,
            responsable TEXT,
            activo BOOLEAN DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (departamento_id) REFERENCES departamentos(id),
            FOREIGN KEY (subdepartamento_id) REFERENCES subdepartamentos(id),
            FOREIGN KEY (punto_venta_id) REFERENCES puntos_venta(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS registros_temperatura (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equipo_id INTEGER NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            temperatura REAL NOT NULL,
            usuario TEXT NOT NULL,
            observaciones TEXT,
            fuera_rango BOOLEAN DEFAULT 0,
            incidencia_id INTEGER,
            equipo_mantenimiento_id INTEGER,
            almacen_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (equipo_id) REFERENCES equipos(id),
            FOREIGN KEY (incidencia_id) REFERENCES incidencias(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS plan_limpieza (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area TEXT NOT NULL,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            tarea TEXT NOT NULL,
            frecuencia TEXT,
            responsable TEXT,
            productos TEXT,
            instrucciones TEXT,
            activo BOOLEAN DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (departamento_id) REFERENCES departamentos(id),
            FOREIGN KEY (subdepartamento_id) REFERENCES subdepartamentos(id),
            FOREIGN KEY (punto_venta_id) REFERENCES puntos_venta(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS registros_limpieza (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            realizada_por TEXT NOT NULL,
            verificada_por TEXT,
            observaciones TEXT,
            incidencia_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (plan_id) REFERENCES plan_limpieza(id),
            FOREIGN KEY (incidencia_id) REFERENCES incidencias(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        # ============================================
        # INCIDENCIAS Y ALERTAS
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS incidencias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            referencia_id INTEGER,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            usuario TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            gravedad TEXT DEFAULT 'media',
            estado TEXT DEFAULT 'abierta',
            solucion TEXT,
            fecha_resolucion DATETIME,
            resuelto_por TEXT,
            observaciones TEXT,
            origen_modulo TEXT,
            destino_modulo TEXT,
            categoria_operativa TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            ubicacion TEXT,
            habitacion_id INTEGER,
            zona_id INTEGER,
            equipo_mantenimiento_id INTEGER,
            equipo_appcc_id INTEGER,
            orden_trabajo_id INTEGER,
            requiere_mantenimiento INTEGER DEFAULT 0,
            requiere_appcc INTEGER DEFAULT 0,
            origen_tabla TEXT,
            origen_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS alertas_caducidad (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha_alerta DATE,
            tipo TEXT,
            notificado BOOLEAN DEFAULT 0,
            resuelto BOOLEAN DEFAULT 0,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (lote_id) REFERENCES lotes(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS alertas_programadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            lote_id INTEGER,
            parte_id INTEGER,
            fecha_alerta DATETIME NOT NULL,
            fecha_produccion DATE,
            mensaje TEXT NOT NULL,
            enviada BOOLEAN DEFAULT 0,
            resuelta BOOLEAN DEFAULT 0,
            usuario_destino_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (lote_id) REFERENCES lotes(id),
            FOREIGN KEY (parte_id) REFERENCES partes_produccion(id),
            FOREIGN KEY (usuario_destino_id) REFERENCES usuarios(id)
        );
        """)

        # ============================================
        # CALENDARIO DE PROVEEDORES
        # ============================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS proveedor_calendario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor_id INTEGER NOT NULL,
            dia_semana INTEGER,
            dia_mes INTEGER,
            tipo TEXT NOT NULL,
            hora TIME,
            activo BOOLEAN DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (proveedor_id) REFERENCES proveedores(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS calendario_excepciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor_id INTEGER,
            fecha DATE NOT NULL,
            motivo TEXT,
            tipo TEXT DEFAULT 'no_laborable',
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (proveedor_id) REFERENCES proveedores(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS pedidos_programados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor_id INTEGER NOT NULL,
            producto_id INTEGER,
            frecuencia TEXT NOT NULL,
            dia_semana INTEGER,
            dia_mes INTEGER,
            semana_referencia INTEGER,
            cantidad REAL,
            activo BOOLEAN DEFAULT 1,
            ultimo_pedido DATE,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (proveedor_id) REFERENCES proveedores(id),
            FOREIGN KEY (producto_id) REFERENCES productos(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        );
        """)

        # ============================================
        # DATOS POR DEFECTO
        # ============================================

        cursor.execute("""
        INSERT OR IGNORE INTO empresas (id, nombre, nif, direccion, telefono, email, activo)
        VALUES (1, 'Empresa Principal', 'B12345678', 'C/ Principal 1', '912345678', 'info@empresa.com', 1)
        """)

        departamentos_seed = [
            (1, 'COCINA CENTRAL', 'Cocina principal y preparaciones', 1, 1),
            (2, 'BARES', 'Servicio de barra y bebidas', 1, 1),
            (3, 'RESTAURANTES', 'Servicio de restaurante y buffet', 1, 1),
            (5, 'RECEPCION', 'Reservas, check-in, caja y atencion al cliente', 1, 1),
            (6, 'RRHH', 'Personal, fichajes, documentacion y solicitudes', 1, 1),
            (7, 'PISOS', 'Limpieza, lavanderia y amenities', 1, 1),
            (8, 'MANTENIMIENTO', 'Averias, instalaciones y consumibles tecnicos', 1, 1),
            (9, 'ADMINISTRACION', 'Facturacion, contabilidad y direccion', 1, 1),
            (10, 'COMPRAS / ALMACEN', 'Compras, recepciones y control de almacenes', 1, 1),
            (4, 'SNACK', 'Servicio rápido y snacks', 1, 1),
        ]
        cursor.executemany("""
            INSERT OR IGNORE INTO departamentos (id, nombre, descripcion, activo, empresa_id)
            VALUES (?, ?, ?, ?, ?)
        """, departamentos_seed)

        if _column_exists(cursor, "almacenes", "departamento_id"):
            cursor.execute("""
            INSERT OR IGNORE INTO almacenes (id, nombre, tipo, ubicacion, responsable, departamento_id, activo, empresa_id)
            VALUES (1, 'Almacén Central', 'seco', 'Principal', 'Sistema', 1, 1, 1)
            """)
        else:
            cursor.execute("""
            INSERT OR IGNORE INTO almacenes (id, nombre, tipo, ubicacion, responsable, activo, empresa_id)
            VALUES (1, 'Almacén Central', 'seco', 'Principal', 'Sistema', 1, 1)
            """)

        if _column_exists(cursor, "almacenes", "departamento_id"):
            almacenes_hotel_seed = [
                (2, 'Almacen Recepcion', 'oficina', 'Recepcion', 'Recepcion', 5, 1, 1),
                (3, 'Almacen RRHH', 'oficina', 'Administracion', 'RRHH', 6, 1, 1),
                (4, 'Almacen Pisos', 'lenceria', 'Office pisos', 'Gobernanta', 7, 1, 1),
                (5, 'Almacen Mantenimiento', 'tecnico', 'Taller', 'Mantenimiento', 8, 1, 1),
                (6, 'Almacen Administracion', 'oficina', 'Administracion', 'Administracion', 9, 1, 1),
            ]
            cursor.executemany("""
            INSERT OR IGNORE INTO almacenes (id, nombre, tipo, ubicacion, responsable, departamento_id, activo, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, almacenes_hotel_seed)
        else:
            almacenes_hotel_seed = [
                (2, 'Almacen Recepcion', 'oficina', 'Recepcion', 'Recepcion', 1, 1),
                (3, 'Almacen RRHH', 'oficina', 'Administracion', 'RRHH', 1, 1),
                (4, 'Almacen Pisos', 'lenceria', 'Office pisos', 'Gobernanta', 1, 1),
                (5, 'Almacen Mantenimiento', 'tecnico', 'Taller', 'Mantenimiento', 1, 1),
                (6, 'Almacen Administracion', 'oficina', 'Administracion', 'Administracion', 1, 1),
            ]
            cursor.executemany("""
            INSERT OR IGNORE INTO almacenes (id, nombre, tipo, ubicacion, responsable, activo, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, almacenes_hotel_seed)

        subdepartamentos_seed = [
            (1, 'COCINA CALIENTE', 1, 'Platos calientes, parrilla, arroces', 1, 1),
            (2, 'COCINA FRIA', 1, 'Ensaladas, entrantes fríos, buffet frío', 1, 1),
            (3, 'PASTELERIA', 1, 'Postres, repostería, helados', 1, 1),
            (4, 'DESAYUNOS', 1, 'Desayunos, bollería, cafetería', 1, 1),
            (5, 'BAR PISCINA', 2, 'Servicio de piscina y terraza', 1, 1),
            (6, 'BAR LOBBY', 2, 'Bar principal del hotel', 1, 1),
            (7, 'RESTAURANTE PRINCIPAL', 3, 'Carta y buffet', 1, 1),
            (9, 'RESERVAS', 5, 'Reservas, canales y cupos', 1, 1),
            (10, 'CHECK-IN / CHECK-OUT', 5, 'Llegadas, salidas y caja de recepcion', 1, 1),
            (11, 'ADMINISTRACION RRHH', 6, 'Contratos, documentacion y nominas', 1, 1),
            (12, 'LAVANDERIA', 7, 'Ropa, lenceria y lavanderia', 1, 1),
            (13, 'LIMPIEZA HABITACIONES', 7, 'Limpieza de habitaciones y zonas comunes', 1, 1),
            (14, 'AVERIAS', 8, 'Partes de averias y mantenimiento correctivo', 1, 1),
            (15, 'CONTABILIDAD', 9, 'Facturacion y contabilidad', 1, 1),
            (16, 'COMPRAS', 10, 'Pedidos, proveedores y validaciones', 1, 1),
            (8, 'SNACK BAR', 4, 'Comida rápida y snacks', 1, 1),
        ]
        cursor.executemany("""
            INSERT OR IGNORE INTO subdepartamentos (id, nombre, departamento_id, descripcion, activo, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, subdepartamentos_seed)

        puntos_venta_seed = [
            (1, 'Buffet desayuno', 'buffet', 1, 1, 1),
            (2, 'Buffet comida', 'buffet', 1, 1, 1),
            (3, 'Carta noche', 'carta', 1, 1, 1),
            (4, 'Bar piscina', 'bar', 1, 1, 1),
            (5, 'Servicio habitaciones', 'habitaciones', 1, 1, 1),
        ]
        cursor.executemany("""
            INSERT OR IGNORE INTO puntos_venta (id, nombre, tipo, almacen_id, activo, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, puntos_venta_seed)

        tipos_menu_seed = [
            (1, 'DESAYUNO', 1, 1, 1),
            (2, 'ALMUERZO', 2, 1, 1),
            (3, 'CENA', 3, 1, 1),
            (4, 'POSTRE', 4, 1, 1),
            (5, 'SNACK', 4, 1, 1),
        ]
        cursor.executemany("""
            INSERT OR IGNORE INTO tipos_menu (id, nombre, punto_venta_id, activo, empresa_id)
            VALUES (?, ?, ?, ?, ?)
        """, tipos_menu_seed)

        roles_seed = [
            ('admin',),
            ('gerente',),
            ('direccion',),
            ('director_operaciones',),
            ('director_hotel',),
            ('subdirector',),
            ('contable',),
            ('rrhh',),
            ('recursos_humanos',),
            ('compras',),
            ('economato',),
            ('jefe_compras',),
            ('responsable_compras',),
            ('empleado_compras',),
            ('auxiliar_compras',),
            ('contabilidad',),
            ('administracion',),
            ('jefe_departamento',),
            ('segundo_jefe',),
            ('jefe_cocina',),
            ('chef',),
            ('jefe_partida',),
            ('cocinero',),
            ('recepcion',),
            ('jefe_recepcion',),
            ('mantenimiento',),
            ('jefe_mantenimiento',),
            ('segundo_mantenimiento',),
            ('pisos',),
            ('gobernanta',),
            ('supervisora',),
            ('camarera',),
            ('lavanderia',),
            ('tablet_pisos',),
            ('empleado',),
            ('kiosco',),
        ]
        cursor.executemany("""
            INSERT OR IGNORE INTO roles (nombre) VALUES (?)
        """, roles_seed)

        # ============================================
        # COLUMNAS FALTANTES EN TABLAS EXISTENTES
        # ============================================

        _safe_add_column(cursor, "departamentos", "empresa_id INTEGER DEFAULT 1")
        _safe_add_column(cursor, "proveedores", "centro_id INTEGER")
        _safe_add_column(cursor, "proveedores", "email_pedidos TEXT")
        _safe_add_column(cursor, "menus_diarios", "centro_id INTEGER")
        _safe_add_column(cursor, "departamentos", "centro_id INTEGER")
        _safe_add_column(cursor, "subdepartamentos", "centro_id INTEGER")
        _safe_add_column(cursor, "almacenes", "centro_id INTEGER")
        _safe_add_column(cursor, "puntos_venta", "centro_id INTEGER")
        _safe_add_column(cursor, "empleados", "centro_id INTEGER")
        _safe_add_column(cursor, "empleados", "tipo_empleado TEXT DEFAULT 'real'")
        _safe_add_column(cursor, "empleados", "origen_importacion TEXT")
        _safe_add_column(cursor, "empleados", "codigo_importacion TEXT")
        _safe_add_column(cursor, "empleados", "motivo_cambio TEXT")
        _safe_add_column(cursor, "usuarios", "empleado_id INTEGER")
        _safe_add_column(cursor, "usuarios", "codigo_acceso TEXT")
        _safe_add_column(cursor, "usuarios", "debe_cambiar_password INTEGER DEFAULT 0")
        _safe_add_column(cursor, "usuarios", "es_global INTEGER DEFAULT 0")
        _safe_add_column(cursor, "usuarios", "puede_ver_precios INTEGER DEFAULT 0")
        _safe_add_column(cursor, "usuarios", "puede_modificar_precios INTEGER DEFAULT 0")
        _safe_add_column(cursor, "usuarios", "puede_validar_recepcion INTEGER DEFAULT 0")
        _safe_add_column(cursor, "usuarios", "puede_validar_economicamente INTEGER DEFAULT 0")
        cursor.execute("""
            UPDATE usuarios
            SET codigo_acceso = 'U' || printf('%05d', id)
            WHERE codigo_acceso IS NULL OR TRIM(codigo_acceso) = ''
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS registros_deterioro_lotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            cantidad REAL NOT NULL,
            motivo TEXT,
            aspecto TEXT,
            olor TEXT,
            temperatura REAL,
            decision TEXT,
            destino TEXT,
            responsable TEXT,
            incidencia_id INTEGER,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            FOREIGN KEY (lote_id) REFERENCES lotes(id),
            FOREIGN KEY (incidencia_id) REFERENCES incidencias(id)
        );
        """)
        _safe_add_column(cursor, "subdepartamentos", "empresa_id INTEGER DEFAULT 1")
        _safe_add_column(cursor, "puntos_venta", "empresa_id INTEGER DEFAULT 1")
        _safe_add_column(cursor, "tipos_menu", "empresa_id INTEGER DEFAULT 1")
        _safe_add_column(cursor, "movimientos_lotes", "empresa_id INTEGER DEFAULT 1")
        _safe_add_column(cursor, "historial_mantenimiento", "empresa_id INTEGER DEFAULT 1")

        # Compatibilidad con lineas_produccion antiguas/nuevas
        _safe_add_column(cursor, "lineas_produccion", "cantidad_planificada REAL")
        _safe_add_column(cursor, "lineas_produccion", "cantidad_real REAL")
        _safe_add_column(cursor, "lineas_produccion", "cantidad_producida REAL")
        _safe_add_column(cursor, "lineas_produccion", "coste_estimado REAL")
        _safe_add_column(cursor, "lineas_produccion", "coste_real REAL")
        _safe_add_column(cursor, "lineas_produccion", "coste_unitario_teorico REAL")
        _safe_add_column(cursor, "lineas_produccion", "observaciones TEXT")
        _safe_add_column(cursor, "lineas_produccion", "empresa_id INTEGER DEFAULT 1")

        # Compatibilidad con lotes: coste real recibido por lote
        _safe_add_column(cursor, "lotes", "coste_unitario REAL DEFAULT 0")
        _safe_add_column(cursor, "lotes", "coste_total REAL DEFAULT 0")
        _safe_add_column(cursor, "lotes", "moneda TEXT DEFAULT 'EUR'")

        # Compatibilidad con consumo_produccion: coste real trazable por lote/producto
        _safe_add_column(cursor, "consumo_produccion", "producto_id INTEGER")
        _safe_add_column(cursor, "consumo_produccion", "coste_unitario REAL DEFAULT 0")
        _safe_add_column(cursor, "consumo_produccion", "coste_total REAL DEFAULT 0")
        _safe_add_column(cursor, "consumo_produccion", "empresa_id INTEGER DEFAULT 1")

        # Compatibilidad con partes_produccion
        _safe_add_column(cursor, "partes_produccion", "fecha_prevista DATE")
        _safe_add_column(cursor, "partes_produccion", "hora_inicio DATETIME")
        _safe_add_column(cursor, "partes_produccion", "hora_fin DATETIME")
        _safe_add_column(cursor, "partes_produccion", "turno TEXT")
        _safe_add_column(cursor, "partes_produccion", "punto_venta_id INTEGER")
        _safe_add_column(cursor, "partes_produccion", "menu_diario_id INTEGER")
        _safe_add_column(cursor, "partes_produccion", "notificado BOOLEAN DEFAULT 0")
        _safe_add_column(cursor, "partes_produccion", "notificado_a TEXT")
        _safe_add_column(cursor, "partes_produccion", "empresa_id INTEGER DEFAULT 1")

        conn.commit()
        print("Base de datos inicializada correctamente")

    except Exception as e:
        conn.rollback()
        print(f"Error en init_db: {e}")
        raise
    finally:
        conn.close()
        
def obtener_o_crear_receta(nombre):
    conn = get_db()

    nombre_limpio = nombre.strip().lower()

    receta = conn.execute("""
        SELECT * FROM recetas
        WHERE LOWER(TRIM(nombre)) = ?
    """, (nombre_limpio,)).fetchone()

    if receta:
        conn.close()
        return receta["id"]

    # crear receta vacía
    cursor = conn.execute("""
        INSERT INTO recetas (nombre, activo, empresa_id)
        VALUES (?, 1, 1)
    """, (nombre.strip(),))

    conn.commit()
    receta_id = cursor.lastrowid
    conn.close()

    return receta_id

        

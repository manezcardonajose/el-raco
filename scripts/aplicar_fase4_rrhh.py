"""
Ejecutar desde la raíz del proyecto:

    python scripts/aplicar_fase4_rrhh.py

Crea/actualiza las tablas de RRHH sin borrar datos.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "pepe4.db"


def table_exists(conn, table_name):
    return conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone() is not None


def column_exists(conn, table_name, column_name):
    return any(row[1] == column_name for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall())


def add_column_if_missing(conn, table_name, column_sql):
    col = column_sql.strip().split()[0]
    if table_exists(conn, table_name) and not column_exists(conn, table_name, col):
        print(f"+ {table_name}.{col}")
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"No existe la base de datos: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS categorias_laborales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        descripcion TEXT,
        salario_base REAL DEFAULT 0,
        convenio TEXT,
        empresa_id INTEGER DEFAULT 1,
        activo INTEGER DEFAULT 1,
        fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    for col in ["salario_base REAL DEFAULT 0", "convenio TEXT", "activo INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP"]:
        add_column_if_missing(conn, "categorias_laborales", col)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS empleados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        apellidos TEXT,
        nif TEXT,
        email TEXT,
        telefono TEXT,
        departamento_id INTEGER,
        subdepartamento_id INTEGER,
        punto_venta_id INTEGER,
        categoria_id INTEGER,
        rol TEXT,
        fecha_alta TEXT,
        fecha_baja TEXT,
        observaciones TEXT,
        empresa_id INTEGER DEFAULT 1,
        activo INTEGER DEFAULT 1
    )
    """)
    for col in [
        "apellidos TEXT", "nif TEXT", "email TEXT", "telefono TEXT", "categoria_id INTEGER", "fecha_alta TEXT",
        "fecha_baja TEXT", "observaciones TEXT", "empresa_id INTEGER DEFAULT 1", "subdepartamento_id INTEGER",
        "punto_venta_id INTEGER", "rol TEXT", "activo INTEGER DEFAULT 1",
        "numero_seguridad_social TEXT", "fecha_nacimiento TEXT", "nacionalidad TEXT", "estado_civil TEXT",
        "direccion TEXT", "codigo_postal TEXT", "localidad TEXT", "provincia TEXT", "pais TEXT DEFAULT 'España'",
        "iban TEXT", "contacto_emergencia_nombre TEXT", "contacto_emergencia_telefono TEXT", "contacto_emergencia_relacion TEXT",
        "nivel_formacion TEXT", "talla_uniforme TEXT", "talla_calzado TEXT", "alergias_observaciones TEXT",
        "carnet_manipulador INTEGER DEFAULT 0", "fecha_caducidad_carnet TEXT", "prl_realizada INTEGER DEFAULT 0",
        "fecha_prl TEXT", "reconocimiento_medico INTEGER DEFAULT 0", "fecha_reconocimiento TEXT"
    ]:
        add_column_if_missing(conn, "empleados", col)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS contratos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        tipo_contrato TEXT,
        fecha_inicio TEXT,
        fecha_fin TEXT,
        jornada TEXT,
        horas_semanales REAL DEFAULT 0,
        salario_bruto REAL DEFAULT 0,
        categoria_id INTEGER,
        estado TEXT DEFAULT 'activo',
        archivo TEXT,
        observaciones TEXT,
        empresa_id INTEGER DEFAULT 1,
        fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    for col in [
        "tipo_contrato TEXT", "fecha_inicio TEXT", "fecha_fin TEXT", "jornada TEXT", "horas_semanales REAL DEFAULT 0",
        "salario_bruto REAL DEFAULT 0", "categoria_id INTEGER", "estado TEXT DEFAULT 'activo'", "archivo TEXT",
        "observaciones TEXT", "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "centro_trabajo TEXT", "puesto_trabajo TEXT", "grupo_profesional TEXT", "convenio TEXT", "tipo_jornada TEXT",
        "turno TEXT", "dias_trabajo TEXT", "periodo_prueba TEXT", "salario_bruto_anual REAL DEFAULT 0",
        "salario_bruto_mensual REAL DEFAULT 0", "pagas INTEGER DEFAULT 14", "vacaciones_dias REAL DEFAULT 30",
        "fecha_preaviso TEXT", "motivo_fin TEXT"
    ]:
        add_column_if_missing(conn, "contratos", col)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS nominas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        mes INTEGER NOT NULL,
        anio INTEGER NOT NULL,
        bruto REAL DEFAULT 0,
        neto REAL DEFAULT 0,
        coste_empresa REAL DEFAULT 0,
        estado TEXT DEFAULT 'pendiente',
        archivo TEXT,
        observaciones TEXT,
        empresa_id INTEGER DEFAULT 1,
        fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    for col in [
        "bruto REAL DEFAULT 0", "coste_empresa REAL DEFAULT 0", "estado TEXT DEFAULT 'pendiente'", "archivo TEXT", "observaciones TEXT",
        "periodo_desde TEXT", "periodo_hasta TEXT", "salario_base REAL DEFAULT 0", "complementos REAL DEFAULT 0",
        "horas_extra REAL DEFAULT 0", "pluses REAL DEFAULT 0", "prorrata_pagas REAL DEFAULT 0", "irpf REAL DEFAULT 0",
        "seguridad_social_trabajador REAL DEFAULT 0", "seguridad_social_empresa REAL DEFAULT 0", "anticipos_embargos REAL DEFAULT 0",
        "fecha_pago TEXT", "forma_pago TEXT"
    ]:
        add_column_if_missing(conn, "nominas", col)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS fichajes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        fecha_hora TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        origen TEXT DEFAULT 'manual',
        observaciones TEXT,
        empresa_id INTEGER DEFAULT 1,
        usuario_id INTEGER
    )
    """)
    for col in ["tipo TEXT", "fecha_hora TEXT DEFAULT CURRENT_TIMESTAMP", "origen TEXT DEFAULT 'manual'", "observaciones TEXT", "empresa_id INTEGER DEFAULT 1", "usuario_id INTEGER"]:
        add_column_if_missing(conn, "fichajes", col)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS usuario_ambitos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER NOT NULL,
        departamento_id INTEGER,
        subdepartamento_id INTEGER,
        punto_venta_id INTEGER,
        tipo_acceso TEXT DEFAULT 'departamento'
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS documentos_rrhh (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        tipo_documento TEXT NOT NULL,
        nombre TEXT,
        fecha_documento TEXT,
        fecha_caducidad TEXT,
        archivo TEXT,
        estado TEXT DEFAULT 'vigente',
        observaciones TEXT,
        empresa_id INTEGER DEFAULT 1,
        fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    for col in ["tipo_documento TEXT", "nombre TEXT", "fecha_documento TEXT", "fecha_caducidad TEXT", "archivo TEXT", "estado TEXT DEFAULT 'vigente'", "observaciones TEXT", "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP"]:
        add_column_if_missing(conn, "documentos_rrhh", col)

    # Permisos mínimos útiles para esta fase, sin borrar los existentes.
    conn.execute("CREATE TABLE IF NOT EXISTS permisos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT UNIQUE, descripcion TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS roles (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT UNIQUE)")
    conn.execute("CREATE TABLE IF NOT EXISTS roles_permisos (rol_id INTEGER, permiso_id INTEGER)")
    permisos = [
        ("ver_rrhh", "Acceso al módulo RRHH"),
        ("gestionar_empleados", "Crear y editar empleados"),
        ("ver_contratos", "Ver contratos"),
        ("gestionar_contratos", "Crear y editar contratos"),
        ("ver_nominas", "Ver nóminas"),
        ("gestionar_nominas", "Crear y editar nóminas"),
        ("ver_fichajes", "Ver fichajes"),
        ("gestionar_fichajes", "Registrar y corregir fichajes"),
        ("acceso_global", "Ver todos los departamentos"),
    ]
    for nombre, descripcion in permisos:
        conn.execute("INSERT OR IGNORE INTO permisos (nombre, descripcion) VALUES (?, ?)", (nombre, descripcion))
    for rol in ["admin", "direccion", "rrhh", "jefe_departamento", "empleado"]:
        conn.execute("INSERT OR IGNORE INTO roles (nombre) VALUES (?)", (rol,))



    # PRE-NÓMINA: turnos, variables y resumen mensual estimado.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS turnos_rrhh (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        fecha TEXT NOT NULL,
        hora_inicio TEXT,
        hora_fin TEXT,
        tipo TEXT DEFAULT 'normal',
        departamento_id INTEGER,
        observaciones TEXT,
        empresa_id INTEGER DEFAULT 1,
        fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    for col in [
        "empleado_id INTEGER", "fecha TEXT", "hora_inicio TEXT", "hora_fin TEXT", "tipo TEXT DEFAULT 'normal'",
        "departamento_id INTEGER", "observaciones TEXT", "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "motivo_libre TEXT"
    ]:
        add_column_if_missing(conn, "turnos_rrhh", col)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS variables_prenomina (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        fecha TEXT NOT NULL,
        concepto TEXT NOT NULL,
        tipo TEXT DEFAULT 'plus',
        unidades REAL DEFAULT 1,
        importe_unitario REAL DEFAULT 0,
        importe_total REAL DEFAULT 0,
        observaciones TEXT,
        empresa_id INTEGER DEFAULT 1,
        fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    for col in [
        "empleado_id INTEGER", "fecha TEXT", "concepto TEXT", "tipo TEXT DEFAULT 'plus'", "unidades REAL DEFAULT 1",
        "importe_unitario REAL DEFAULT 0", "importe_total REAL DEFAULT 0", "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP"
    ]:
        add_column_if_missing(conn, "variables_prenomina", col)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS resumen_prenomina (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        mes INTEGER NOT NULL,
        anio INTEGER NOT NULL,
        horas_normales REAL DEFAULT 0,
        horas_extra REAL DEFAULT 0,
        horas_nocturnas REAL DEFAULT 0,
        horas_festivas REAL DEFAULT 0,
        ausencias_horas REAL DEFAULT 0,
        salario_base_mes REAL DEFAULT 0,
        precio_hora REAL DEFAULT 0,
        pluses REAL DEFAULT 0,
        descuentos REAL DEFAULT 0,
        coste_estimado REAL DEFAULT 0,
        observaciones TEXT,
        estado TEXT DEFAULT 'borrador',
        empresa_id INTEGER DEFAULT 1,
        fecha_calculo TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(empleado_id, mes, anio, empresa_id)
    )
    """)
    for col in [
        "horas_normales REAL DEFAULT 0", "horas_extra REAL DEFAULT 0", "horas_nocturnas REAL DEFAULT 0",
        "horas_festivas REAL DEFAULT 0", "ausencias_horas REAL DEFAULT 0", "salario_base_mes REAL DEFAULT 0",
        "precio_hora REAL DEFAULT 0", "pluses REAL DEFAULT 0", "descuentos REAL DEFAULT 0",
        "coste_estimado REAL DEFAULT 0", "observaciones TEXT", "estado TEXT DEFAULT 'borrador'",
        "empresa_id INTEGER DEFAULT 1", "fecha_calculo TEXT DEFAULT CURRENT_TIMESTAMP"
    ]:
        add_column_if_missing(conn, "resumen_prenomina", col)

    permisos_prenomina = [
        ("ver_prenomina", "Ver cálculos de pre-nómina"),
        ("gestionar_prenomina", "Calcular y cerrar pre-nóminas"),
        ("gestionar_turnos", "Crear y editar turnos RRHH"),
        ("gestionar_variables_prenomina", "Crear variables de pre-nómina"),
    ]
    for nombre, descripcion in permisos_prenomina:
        conn.execute("INSERT OR IGNORE INTO permisos (nombre, descripcion) VALUES (?, ?)", (nombre, descripcion))



    # Cuadrantes y vacaciones
    conn.execute("""
    CREATE TABLE IF NOT EXISTS vacaciones_rrhh (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        fecha_inicio TEXT NOT NULL,
        fecha_fin TEXT NOT NULL,
        tipo TEXT DEFAULT 'vacaciones',
        estado TEXT DEFAULT 'solicitada',
        dias REAL DEFAULT 0,
        observaciones TEXT,
        empresa_id INTEGER DEFAULT 1,
        usuario_creacion_id INTEGER,
        fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    for col in [
        "empleado_id INTEGER", "fecha_inicio TEXT", "fecha_fin TEXT", "tipo TEXT DEFAULT 'vacaciones'",
        "estado TEXT DEFAULT 'solicitada'", "dias REAL DEFAULT 0", "observaciones TEXT", "empresa_id INTEGER DEFAULT 1",
        "usuario_creacion_id INTEGER", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP"
    ]:
        add_column_if_missing(conn, "vacaciones_rrhh", col)

    # Cuadrante real: centros, puestos y necesidades. No impone horarios.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS centros_trabajo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        codigo TEXT,
        direccion TEXT,
        descripcion TEXT,
        empresa_id INTEGER DEFAULT 1,
        activo INTEGER DEFAULT 1,
        fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    for col in ["nombre TEXT", "codigo TEXT", "direccion TEXT", "descripcion TEXT", "empresa_id INTEGER DEFAULT 1", "activo INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP"]:
        add_column_if_missing(conn, "centros_trabajo", col)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS puestos_operativos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        descripcion TEXT,
        centro_id INTEGER,
        departamento_id INTEGER,
        subdepartamento_id INTEGER,
        categoria_id INTEGER,
        empresa_id INTEGER DEFAULT 1,
        activo INTEGER DEFAULT 1,
        fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    for col in ["nombre TEXT", "descripcion TEXT", "centro_id INTEGER", "departamento_id INTEGER", "subdepartamento_id INTEGER", "categoria_id INTEGER", "empresa_id INTEGER DEFAULT 1", "activo INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP"]:
        add_column_if_missing(conn, "puestos_operativos", col)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS necesidades_operativas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        centro_id INTEGER,
        departamento_id INTEGER,
        subdepartamento_id INTEGER,
        puesto_id INTEGER,
        dia_semana INTEGER NOT NULL,
        personas_necesarias INTEGER DEFAULT 1,
        observaciones TEXT,
        empresa_id INTEGER DEFAULT 1,
        activo INTEGER DEFAULT 1,
        fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    for col in ["centro_id INTEGER", "departamento_id INTEGER", "subdepartamento_id INTEGER", "puesto_id INTEGER", "dia_semana INTEGER", "personas_necesarias INTEGER DEFAULT 1", "observaciones TEXT", "empresa_id INTEGER DEFAULT 1", "activo INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP"]:
        add_column_if_missing(conn, "necesidades_operativas", col)

    for col in ["centro_id INTEGER", "subdepartamento_id INTEGER", "puesto_id INTEGER", "estado TEXT DEFAULT 'planificado'", "origen TEXT DEFAULT 'cuadrante_real'", "motivo_libre TEXT"]:
        add_column_if_missing(conn, "turnos_rrhh", col)

    permisos_cuadrantes = [
        ("ver_cuadrantes", "Ver cuadrantes de horarios"),
        ("gestionar_cuadrantes", "Crear y editar cuadrantes de horarios"),
        ("ver_vacaciones", "Ver vacaciones y ausencias"),
        ("gestionar_vacaciones", "Crear, aprobar y rechazar vacaciones"),
    ]
    for nombre, descripcion in permisos_cuadrantes:
        conn.execute("INSERT OR IGNORE INTO permisos (nombre, descripcion) VALUES (?, ?)", (nombre, descripcion))



    # Capa editable completa: histórico y columnas de modificación
    conn.execute("""
    CREATE TABLE IF NOT EXISTS rrhh_historial_cambios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tabla TEXT NOT NULL,
        registro_id INTEGER NOT NULL,
        accion TEXT NOT NULL,
        motivo TEXT,
        usuario_id INTEGER,
        empresa_id INTEGER DEFAULT 1,
        fecha TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    tablas_editables = [
        "empleados", "categorias_laborales", "contratos", "nominas", "documentos_rrhh", "fichajes",
        "turnos_rrhh", "variables_prenomina", "vacaciones_rrhh", "centros_trabajo", "puestos_operativos", "necesidades_operativas",
    ]
    for tabla in tablas_editables:
        add_column_if_missing(conn, tabla, "updated_at TEXT")
        add_column_if_missing(conn, tabla, "created_by INTEGER")
        add_column_if_missing(conn, tabla, "updated_by INTEGER")
        add_column_if_missing(conn, tabla, "motivo_cambio TEXT")
    for tabla in ["documentos_rrhh", "fichajes", "turnos_rrhh", "variables_prenomina", "vacaciones_rrhh"]:
        add_column_if_missing(conn, tabla, "estado TEXT DEFAULT 'activo'")

    conn.commit()
    conn.close()
    print("FASE 4 RRHH aplicada correctamente sin borrar datos.")


if __name__ == "__main__":
    main()

"""Modelo preparado de autorizaciones; su activacion requiere migracion explicita.

Estas funciones no abren conexiones ni confirman transacciones. El llamador debe
ejecutarlas en una transaccion controlada y registrar la auditoria pertinente.
"""


def crear_esquema_acceso_empresas(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS administradores_instalacion (
        usuario_id INTEGER PRIMARY KEY REFERENCES usuarios(id),
        creado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS autorizaciones_empresa (
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
        empresa_id INTEGER NOT NULL REFERENCES empresas(id),
        creado_por INTEGER NOT NULL REFERENCES usuarios(id),
        creado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (usuario_id, empresa_id)
    )""")


def inicializar_administrador_instalacion(conn, usuario_id):
    """Designa el primer administrador, solo cuando no existe ninguno."""
    if conn.execute('SELECT 1 FROM administradores_instalacion LIMIT 1').fetchone():
        raise ValueError('La instalacion ya tiene administrador; no se puede inicializar de nuevo')
    usuario = conn.execute('SELECT 1 FROM usuarios WHERE id=? AND COALESCE(activo, 1)=1', (usuario_id,)).fetchone()
    if not usuario:
        raise ValueError('El administrador inicial debe ser un usuario activo')
    conn.execute('INSERT INTO administradores_instalacion (usuario_id) VALUES (?)', (usuario_id,))


def es_administrador_instalacion(conn, usuario_id):
    return bool(conn.execute('''SELECT 1 FROM administradores_instalacion a
        JOIN usuarios u ON u.id=a.usuario_id
        WHERE a.usuario_id=? AND COALESCE(u.activo, 1)=1''', (usuario_id,)).fetchone())


def usuario_puede_acceder_empresa(conn, usuario_id, empresa_id):
    empresa = conn.execute('SELECT 1 FROM empresas WHERE id=? AND COALESCE(activo, 1)=1', (empresa_id,)).fetchone()
    if not empresa:
        return False
    if es_administrador_instalacion(conn, usuario_id):
        return True
    return bool(conn.execute('''SELECT 1 FROM autorizaciones_empresa a
        JOIN usuarios u ON u.id=a.usuario_id
        WHERE a.usuario_id=? AND a.empresa_id=? AND COALESCE(u.activo, 1)=1''',
        (usuario_id, empresa_id)).fetchone())


def autorizar_empresa(conn, administrador_id, usuario_id, empresa_id):
    if not es_administrador_instalacion(conn, administrador_id):
        raise PermissionError('Solo un administrador de la instalacion puede autorizar empresas')
    if not conn.execute('SELECT 1 FROM usuarios WHERE id=? AND COALESCE(activo, 1)=1', (usuario_id,)).fetchone():
        raise ValueError('El usuario no existe o esta inactivo')
    if not conn.execute('SELECT 1 FROM empresas WHERE id=? AND COALESCE(activo, 1)=1', (empresa_id,)).fetchone():
        raise ValueError('La empresa no existe o esta inactiva')
    conn.execute('INSERT OR IGNORE INTO autorizaciones_empresa (usuario_id, empresa_id, creado_por) VALUES (?, ?, ?)',
                 (usuario_id, empresa_id, administrador_id))

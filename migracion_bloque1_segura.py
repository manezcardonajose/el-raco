import sqlite3
from pathlib import Path

DB_PATH = Path('pepe4.db')


def column_exists(cur, table, column):
    cur.execute(f'PRAGMA table_info({table})')
    return any(row[1] == column for row in cur.fetchall())


def table_exists(cur, table):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def add_column_if_missing(cur, table, definition):
    col = definition.split()[0]
    if not column_exists(cur, table, col):
        cur.execute(f'ALTER TABLE {table} ADD COLUMN {definition}')
        print(f'[OK] Añadida columna {table}.{col}')
    else:
        print(f'[SKIP] Ya existe {table}.{col}')


def main():
    if not DB_PATH.exists():
        raise SystemExit(f'No se encuentra la base de datos: {DB_PATH.resolve()}')

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    try:
        if table_exists(cur, 'recepciones'):
            add_column_if_missing(cur, 'recepciones', 'empresa_id INTEGER DEFAULT 1')
            cur.execute('UPDATE recepciones SET empresa_id = 1 WHERE empresa_id IS NULL')

        if table_exists(cur, 'lineas_recepcion'):
            add_column_if_missing(cur, 'lineas_recepcion', 'precio_unitario REAL')
            add_column_if_missing(cur, 'lineas_recepcion', 'importe_total REAL')
            add_column_if_missing(cur, 'lineas_recepcion', 'aceptado BOOLEAN DEFAULT 1')
            add_column_if_missing(cur, 'lineas_recepcion', 'motivo_rechazo TEXT')
            add_column_if_missing(cur, 'lineas_recepcion', 'incidencia_id INTEGER')
            add_column_if_missing(cur, 'lineas_recepcion', 'empresa_id INTEGER DEFAULT 1')
            cur.execute('UPDATE lineas_recepcion SET empresa_id = 1 WHERE empresa_id IS NULL')
            cur.execute('UPDATE lineas_recepcion SET aceptado = 1 WHERE aceptado IS NULL')

        cur.execute(
            '''
            CREATE TABLE IF NOT EXISTS punto_venta_departamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                punto_venta_id INTEGER NOT NULL,
                departamento_id INTEGER NOT NULL,
                rol_operativo TEXT DEFAULT 'general',
                FOREIGN KEY (punto_venta_id) REFERENCES puntos_venta(id) ON DELETE CASCADE,
                FOREIGN KEY (departamento_id) REFERENCES departamentos(id)
            )
            '''
        )
        print('[OK] Tabla punto_venta_departamentos verificada')

        if table_exists(cur, 'puntos_venta') and table_exists(cur, 'punto_venta_departamentos'):
            cur.execute(
                '''
                INSERT INTO punto_venta_departamentos (punto_venta_id, departamento_id, rol_operativo)
                SELECT pv.id, pv.departamento_id, 'general'
                FROM puntos_venta pv
                WHERE pv.departamento_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM punto_venta_departamentos pvd
                      WHERE pvd.punto_venta_id = pv.id
                        AND pvd.departamento_id = pv.departamento_id
                  )
                '''
            )
            print(f'[OK] Relaciones heredadas insertadas: {cur.rowcount}')

        conn.commit()
        print('\nMigración completada.')
    finally:
        conn.close()


if __name__ == '__main__':
    main()

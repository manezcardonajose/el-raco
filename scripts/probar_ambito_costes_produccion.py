"""Comprueba resumen y detalle de costes con subdepartamentos aislados."""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix='raco_costes_', ignore_cleanup_errors=True) as folder:
        path = str(Path(folder) / 'prueba.db')
        os.environ['ERP_DATABASE'] = path
        import database
        from flask import Flask, session
        from blueprints.produccion import routes as produccion
        database.init_db()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        produccion._asegurar_planificacion_schema(conn)
        for tabla in ('departamentos', 'subdepartamentos'):
            if 'centro_id' not in produccion._columnas_tabla(conn.cursor(), tabla):
                conn.execute(f'ALTER TABLE {tabla} ADD COLUMN centro_id INTEGER')
        conn.execute("INSERT INTO departamentos (id,nombre,empresa_id,centro_id) VALUES (10001,'Cocina prueba',1,1)")
        conn.executemany("INSERT INTO subdepartamentos (id,nombre,departamento_id,empresa_id,centro_id) VALUES (?, ?, 10001, 1, 1)", [(10001,'Frio'), (10002,'Caliente')])
        conn.executemany("INSERT INTO recetas (id,nombre,empresa_id) VALUES (?, ?, 1)", [(10001,'Plato frio prueba'), (10002,'Plato caliente privado')])
        conn.executemany("INSERT INTO partes_produccion (id,fecha,turno,empresa_id,centro_id,subdepartamento_id) VALUES (?, '2098-01-01', 'almuerzo', 1, 1, ?)", [(10001,10001), (10002,10002)])
        conn.executemany("INSERT INTO lineas_produccion (id,parte_id,receta_id,cantidad_planificada,raciones_planificadas) VALUES (?, ?, ?, 1, ?)", [(10001,10001,10001,7), (10002,10002,10002,99)])
        conn.execute("INSERT INTO productos (id,nombre,precio,empresa_id) VALUES (10001,'Producto prueba',2,1)")
        conn.executemany("INSERT INTO consumo_extra (parte_id,producto_id,cantidad) VALUES (?,10001,?)", [(10001,1),(10002,50)])
        conn.commit()
        app = Flask(__name__)
        app.secret_key = 'prueba-aislada'
        with app.test_request_context('/'):
            session.update(user_rol='cocinero', user_empresa_id=1, centro_activo_id=1, user_departamento_id=10001, user_subdepartamento_id=10001)
            resumen = produccion._resumen_costes_produccion(conn, 1, '2098-01-01', '2098-01-01')
            detalle = produccion._detalle_costes_platos_produccion(conn, 1, '2098-01-01', '2098-01-01')
            assert sum(row['raciones'] for row in resumen) == 7, resumen
            assert sum(row['coste_extra'] for row in resumen) == 2, resumen
            assert len(detalle) == 1 and detalle[0]['plato'] == 'Plato frio prueba', detalle
            session['user_rol'] = 'jefe_cocina'
            resumen = produccion._resumen_costes_produccion(conn, 1, '2098-01-01', '2098-01-01')
            assert sum(row['raciones'] for row in resumen) == 106, resumen
            assert sum(row['coste_extra'] for row in resumen) == 102, resumen
        conn.close()
    print('OK: resumen y detalle de costes respetan subdepartamento y jefatura')


if __name__ == '__main__':
    main()

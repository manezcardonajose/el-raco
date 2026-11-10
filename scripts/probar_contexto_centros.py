"""Pruebas aisladas de seleccion de centro y vista de empresa."""
import sys
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask, session
from helpers.centros_helper import centro_activo_id, filtro_centro_almacen
from helpers.ambito_helper import filtro_ambito_operativo, ids_puntos_venta_ambito
from blueprints.rrhh import routes
from blueprints.auth import routes as auth_routes
from blueprints.produccion import routes as produccion_routes


def sin_decoradores(funcion):
    while hasattr(funcion, '__wrapped__'):
        funcion = funcion.__wrapped__
    return funcion


class ContextoCentrosTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'solo-prueba'

    def test_vista_general_no_recupera_centro_del_empleado(self):
        with self.app.test_request_context('/'):
            session.update(user_centro_id=7, centro_id=8, centro_activo_id=None)
            self.assertIsNone(centro_activo_id())
            session.pop('centro_activo_id')
            self.assertEqual(centro_activo_id(), 7)
            session['centro_activo_id'] = 'invalido'
            self.assertIsNone(centro_activo_id())

    def test_almacen_con_centro_explicito_no_se_filtra_por_departamento_ajeno(self):
        with self.app.test_request_context('/'):
            session['centro_activo_id'] = 1
            conn = sqlite3.connect(':memory:')
            conn.row_factory = sqlite3.Row
            conn.execute('CREATE TABLE departamentos (id INTEGER, empresa_id INTEGER, centro_id INTEGER, activo INTEGER)')
            conn.execute('CREATE TABLE puntos_venta (id INTEGER, empresa_id INTEGER, centro_id INTEGER, activo INTEGER)')
            conn.execute('CREATE TABLE almacenes (id INTEGER, empresa_id INTEGER, centro_id INTEGER, departamento_id INTEGER, punto_venta_id INTEGER)')
            conn.execute('INSERT INTO departamentos VALUES (10, 1, 1, 1)')
            conn.execute('INSERT INTO puntos_venta VALUES (20, 1, 1, 1)')
            conn.executemany('INSERT INTO almacenes VALUES (?, ?, ?, ?, ?)', [
                (1, 1, 1, None, None), (2, 1, None, 10, None),
                (3, 1, 2, 10, None), (4, 1, None, None, 20),
                (5, 2, 1, None, None),
            ])
            filtro, params = filtro_centro_almacen(conn, 'a', 1)
            filas = conn.execute('SELECT id FROM almacenes a WHERE a.empresa_id=?' + filtro + ' ORDER BY id', [1] + params).fetchall()
            self.assertEqual([fila['id'] for fila in filas], [1, 2])
            conn.close()

    def test_productos_produccion_sin_centro_no_amplian_acceso(self):
        with self.app.test_request_context('/'):
            conn = sqlite3.connect(':memory:')
            conn.execute('CREATE TABLE productos (id INTEGER, centro_id INTEGER)')
            conn.executemany('INSERT INTO productos VALUES (?, ?)', [(1, 1), (2, 2), (3, None)])
            filtro, params = produccion_routes._filtro_producto_visible_centro(conn, 'p')
            self.assertEqual(conn.execute('SELECT id FROM productos p WHERE 1=1' + filtro, params).fetchall(), [])
            session['centro_activo_id'] = 1
            filtro, params = produccion_routes._filtro_producto_visible_centro(conn, 'p')
            self.assertEqual(conn.execute('SELECT id FROM productos p WHERE 1=1' + filtro + ' ORDER BY id', params).fetchall(), [(1,), (3,)])
            conn.execute('CREATE TABLE productos_incompletos (id INTEGER)')
            with patch.object(produccion_routes, '_columnas_tabla', return_value=['id']):
                filtro, params = produccion_routes._filtro_producto_visible_centro(conn, 'p')
                self.assertEqual(conn.execute('SELECT id FROM productos p WHERE 1=1' + filtro, params).fetchall(), [])
            conn.close()

    def test_trabajador_sin_departamento_no_hereda_todo_el_centro(self):
        with self.app.test_request_context('/'):
            session.update(user_rol='cocinero', centro_activo_id=1)
            conn = sqlite3.connect(':memory:')
            conn.row_factory = sqlite3.Row
            conn.execute('CREATE TABLE departamentos (id INTEGER, empresa_id INTEGER, centro_id INTEGER, activo INTEGER)')
            conn.execute('CREATE TABLE partes (id INTEGER, empresa_id INTEGER, centro_id INTEGER, departamento_id INTEGER)')
            conn.executemany('INSERT INTO partes VALUES (?, ?, ?, ?)', [(1, 1, 1, 10), (2, 1, 2, 10), (3, 1, None, 10)])
            filtro, params = filtro_ambito_operativo(conn, 'p', 1, 'partes')
            self.assertEqual(conn.execute('SELECT id FROM partes p WHERE ' + filtro, params).fetchall(), [])
            conn.execute('INSERT INTO departamentos VALUES (10, 1, 1, 1)')
            session['user_departamento_id'] = 10
            filtro, params = filtro_ambito_operativo(conn, 'p', 1, 'partes')
            self.assertEqual([r['id'] for r in conn.execute('SELECT id FROM partes p WHERE ' + filtro + ' ORDER BY id', params)], [1])
            conn.close()

    def test_subdepartamento_no_se_amplia_a_otro_del_mismo_departamento(self):
        with self.app.test_request_context('/'):
            session.update(user_rol='cocinero', centro_activo_id=1, user_departamento_id=10, user_subdepartamento_id=100)
            conn = sqlite3.connect(':memory:')
            conn.row_factory = sqlite3.Row
            conn.execute('CREATE TABLE departamentos (id INTEGER, empresa_id INTEGER, centro_id INTEGER, activo INTEGER)')
            conn.execute('CREATE TABLE subdepartamentos (id INTEGER, empresa_id INTEGER, centro_id INTEGER, departamento_id INTEGER, activo INTEGER)')
            conn.execute('CREATE TABLE partes (id INTEGER, empresa_id INTEGER, centro_id INTEGER, departamento_id INTEGER, subdepartamento_id INTEGER)')
            conn.execute('INSERT INTO departamentos VALUES (10, 1, 1, 1)')
            conn.executemany('INSERT INTO subdepartamentos VALUES (?, 1, 1, 10, 1)', [(100,), (101,)])
            conn.executemany('INSERT INTO partes VALUES (?, 1, 1, 10, ?)', [(1, 100), (2, 101), (3, None)])
            filtro, params = filtro_ambito_operativo(conn, 'p', 1, 'partes')
            self.assertEqual([r['id'] for r in conn.execute('SELECT id FROM partes p WHERE ' + filtro, params)], [1])
            session['user_subdepartamento_id'] = 999
            filtro, params = filtro_ambito_operativo(conn, 'p', 1, 'partes')
            self.assertEqual(conn.execute('SELECT id FROM partes p WHERE ' + filtro, params).fetchall(), [])
            session['user_rol'] = 'jefe_cocina'
            filtro, params = filtro_ambito_operativo(conn, 'p', 1, 'partes')
            self.assertEqual([r['id'] for r in conn.execute('SELECT id FROM partes p WHERE ' + filtro + ' ORDER BY id', params)], [1, 2, 3])
            conn.close()

    def test_tabla_antigua_solo_se_abre_con_punto_de_venta_verificable(self):
        with self.app.test_request_context('/'):
            session.update(user_rol='cocinero', centro_activo_id=1, user_departamento_id=10, user_subdepartamento_id=100)
            conn = sqlite3.connect(':memory:')
            conn.row_factory = sqlite3.Row
            conn.execute('CREATE TABLE departamentos (id INTEGER, empresa_id INTEGER, centro_id INTEGER, activo INTEGER)')
            conn.execute('CREATE TABLE subdepartamentos (id INTEGER, empresa_id INTEGER, centro_id INTEGER, departamento_id INTEGER, activo INTEGER)')
            conn.execute('CREATE TABLE puntos_venta (id INTEGER, empresa_id INTEGER, centro_id INTEGER, departamento_id INTEGER, subdepartamento_id INTEGER, activo INTEGER)')
            conn.execute('CREATE TABLE partes (id INTEGER, empresa_id INTEGER, centro_id INTEGER, departamento_id INTEGER, punto_venta_id INTEGER)')
            conn.execute('INSERT INTO departamentos VALUES (10, 1, 1, 1)')
            conn.execute('INSERT INTO subdepartamentos VALUES (100, 1, 1, 10, 1)')
            conn.executemany('INSERT INTO puntos_venta VALUES (?, 1, 1, 10, ?, 1)', [(20, 100), (21, 101)])
            conn.executemany('INSERT INTO partes VALUES (?, 1, 1, 10, ?)', [(1, 20), (2, 21), (3, None)])
            self.assertEqual(ids_puntos_venta_ambito(conn, 1), [20])
            session['user_subdepartamento_id'] = 999
            self.assertEqual(ids_puntos_venta_ambito(conn, 1), [])
            session['user_subdepartamento_id'] = 100
            filtro, params = filtro_ambito_operativo(conn, 'p', 1, 'partes')
            self.assertEqual([r['id'] for r in conn.execute('SELECT id FROM partes p WHERE ' + filtro, params)], [1])
            with patch('helpers.ambito_helper.columna_existe', side_effect=lambda c, t, col: False if (t, col) == ('puntos_venta', 'subdepartamento_id') else col in {r['name'] for r in c.execute(f'PRAGMA table_info({t})')}):
                filtro, params = filtro_ambito_operativo(conn, 'p', 1, 'partes')
                self.assertEqual(conn.execute('SELECT id FROM partes p WHERE ' + filtro, params).fetchall(), [])
            conn.close()

    def test_centro_ajeno_no_cambia_el_centro_seleccionado(self):
        with self.app.test_request_context('/'):
            session.update(user_empresa_id=1, centro_activo_id=3)
            conexion = Mock()
            with patch.object(routes, 'get_db', return_value=conexion), \
                 patch.object(routes, '_ensure_cuadrantes_schema'), \
                 patch.object(routes, '_build_centro_panel', return_value=None) as panel, \
                 patch.object(routes, 'url_for', return_value='/centros'):
                respuesta = sin_decoradores(routes.centro_panel)(99)
            panel.assert_called_once_with(conexion, 1, 99)
            self.assertEqual(respuesta.status_code, 302)
            self.assertEqual(centro_activo_id(), 3)
            conexion.close.assert_called_once()

    def test_centro_valido_se_selecciona_despues_de_comprobarlo(self):
        with self.app.test_request_context('/'):
            session.update(user_empresa_id=1, centro_activo_id=3)
            with patch.object(routes, 'get_db', return_value=Mock()), \
                 patch.object(routes, '_ensure_cuadrantes_schema'), \
                 patch.object(routes, '_build_centro_panel', return_value={'id': 5}), \
                 patch.object(routes, 'render_template', return_value='ok'):
                self.assertEqual(sin_decoradores(routes.centro_panel)(5), 'ok')
            self.assertEqual(centro_activo_id(), 5)

    def test_cambiar_empresa_limpia_contexto_anterior(self):
        with self.app.test_request_context('/', method='POST', data={'empresa_id': '2'}):
            session.update(user_rol='admin', user_empresa_id=1, centro_activo_id=3,
                           user_centro_id=3, centro_id=3, user_empleado_rol='cocina')
            conn = Mock()
            conn.execute.return_value.fetchone.return_value = {'id': 2, 'nombre': 'Empresa prueba'}
            with patch.object(auth_routes, 'get_db', return_value=conn), \
                 patch.object(auth_routes, '_registrar_auditoria_seguridad'), \
                 patch.object(auth_routes, 'url_for', return_value='/'):
                respuesta = sin_decoradores(auth_routes.cambiar_empresa)()
            self.assertEqual(respuesta.status_code, 302)
            self.assertEqual(session['user_empresa_id'], 2)
            self.assertIsNone(centro_activo_id())
            self.assertNotIn('user_centro_id', session)
            self.assertNotIn('centro_id', session)
            self.assertNotIn('user_empleado_rol', session)

    def test_cambiar_empresa_invalida_conserva_contexto(self):
        with self.app.test_request_context('/', method='POST', data={'empresa_id': '2'}):
            session.update(user_rol='admin', user_empresa_id=1, centro_activo_id=3)
            conn = Mock()
            conn.execute.return_value.fetchone.return_value = None
            with patch.object(auth_routes, 'get_db', return_value=conn), \
                 patch.object(auth_routes, 'url_for', return_value='/'):
                respuesta = sin_decoradores(auth_routes.cambiar_empresa)()
            self.assertEqual(respuesta.status_code, 302)
            self.assertEqual(session['user_empresa_id'], 1)
            self.assertEqual(centro_activo_id(), 3)

    def test_direccion_de_centro_no_puede_cambiar_empresa(self):
        for rol in ('director', 'director_hotel', 'subdirector', 'gerente', 'propietario'):
            with self.subTest(rol=rol), self.app.test_request_context('/', method='POST', data={'empresa_id': '2'}):
                session.clear()
                session.update(user_rol=rol, user_es_global=1, user_empresa_id=1, centro_activo_id=3)
                with patch.object(auth_routes, 'get_db') as conectar, \
                     patch.object(auth_routes, 'url_for', return_value='/'):
                    respuesta = sin_decoradores(auth_routes.cambiar_empresa)()
                self.assertEqual(respuesta.status_code, 302)
                conectar.assert_not_called()
                self.assertEqual(session['user_empresa_id'], 1)
                self.assertEqual(centro_activo_id(), 3)


if __name__ == '__main__':
    unittest.main()

"""Comprueba el ambito de pedidos y el detalle ajeno en SQLite temporal."""
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flask import Flask, session
from blueprints.compras import routes as compras


def main():
    prueba = sqlite3.connect(':memory:')
    prueba.row_factory = sqlite3.Row
    prueba.execute('CREATE TABLE lineas_pedido (id INTEGER, pedido_id INTEGER, producto_id INTEGER, cantidad_recibida REAL)')
    prueba.execute('CREATE TABLE recepciones (id INTEGER, pedido_id INTEGER)')
    prueba.execute('CREATE TABLE lineas_recepcion (recepcion_id INTEGER, producto_id INTEGER, cantidad_recibida REAL, aceptado INTEGER)')
    prueba.executemany('INSERT INTO lineas_pedido VALUES (?, ?, ?, ?)', [(1,1,10,0),(2,2,20,1),(3,2,20,2)])
    prueba.executemany('INSERT INTO recepciones VALUES (?, ?)', [(1,1),(2,2)])
    prueba.executemany('INSERT INTO lineas_recepcion VALUES (?, ?, ?, ?)', [(1,10,3,1),(1,10,5,0),(2,20,4,1)])
    compras._recalcular_cantidad_recibida_pedido(prueba.cursor(), 1)
    assert prueba.execute('SELECT cantidad_recibida FROM lineas_pedido WHERE id=1').fetchone()[0] == 3
    try:
        compras._recalcular_cantidad_recibida_pedido(prueba.cursor(), 2)
        raise AssertionError('Se aceptaron lineas de producto ambiguas')
    except ValueError:
        assert [r[0] for r in prueba.execute('SELECT cantidad_recibida FROM lineas_pedido WHERE pedido_id=2 ORDER BY id')] == [1,2]
    prueba.close()
    transaccion = sqlite3.connect(':memory:')
    transaccion.execute('CREATE TABLE lineas_pedido (id INTEGER, pedido_id INTEGER, cantidad_solicitada REAL, cantidad_recibida REAL)')
    transaccion.execute('CREATE TABLE recepciones (id INTEGER)')
    transaccion.execute('INSERT INTO lineas_pedido VALUES (1, 1, 10, 0)')
    transaccion.commit()
    compras._registrar_cantidad_pendiente(transaccion.cursor(), 1, 1, 10)
    transaccion.commit()
    transaccion.execute('INSERT INTO recepciones VALUES (2)')
    try:
        compras._registrar_cantidad_pendiente(transaccion.cursor(), 1, 1, 10)
        raise AssertionError('La segunda recepcion acepto cantidades ya recibidas')
    except ValueError:
        transaccion.rollback()
    assert transaccion.execute('SELECT cantidad_recibida FROM lineas_pedido').fetchone()[0] == 10
    assert transaccion.execute('SELECT COUNT(*) FROM recepciones').fetchone()[0] == 0
    for valor in (float('nan'), float('inf'), -1, 0):
        try:
            compras._registrar_cantidad_pendiente(transaccion.cursor(), 1, 1, valor)
            raise AssertionError('Se acepto una cantidad no valida')
        except ValueError:
            pass
    transaccion.close()
    stock = sqlite3.connect(':memory:')
    stock.row_factory = sqlite3.Row
    stock.execute('CREATE TABLE recepciones (id INTEGER, empresa_id INTEGER, pedido_id INTEGER, stock_aplicado INTEGER)')
    stock.execute('CREATE TABLE pedidos (id INTEGER, numero_pedido TEXT)')
    stock.execute('CREATE TABLE productos (id INTEGER, empresa_id INTEGER, centro_id INTEGER, activo INTEGER, nombre TEXT)')
    stock.execute('CREATE TABLE almacenes (id INTEGER, empresa_id INTEGER, centro_id INTEGER, activo INTEGER)')
    stock.execute('CREATE TABLE lineas_recepcion (id INTEGER, recepcion_id INTEGER, producto_id INTEGER, almacen_id INTEGER, empresa_id INTEGER, cantidad_recibida REAL, aceptado INTEGER)')
    stock.execute('INSERT INTO recepciones VALUES (1,1,NULL,0)')
    stock.execute("INSERT INTO productos VALUES (1,1,1,1,'Producto')")
    stock.execute('INSERT INTO almacenes VALUES (1,1,2,1)')
    stock.execute('INSERT INTO lineas_recepcion VALUES (1,1,1,1,1,5,1)')
    with patch.object(compras, '_scope_recepciones_centro', return_value=('r.empresa_id = ?', [1], 1)):
        try:
            compras._aplicar_stock_recepcion(stock.cursor(), 1, 1)
            raise AssertionError('Se acepto un almacen de otro centro')
        except ValueError:
            assert stock.execute('SELECT stock_aplicado FROM recepciones WHERE id=1').fetchone()[0] == 0
    stock.close()
    with tempfile.TemporaryDirectory(prefix='raco_compras_', ignore_cleanup_errors=True) as carpeta:
        path = str(Path(carpeta) / 'prueba.db')
        def conectar():
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            return conn
        conn = conectar()
        conn.execute('CREATE TABLE lineas_pedido (id INTEGER, pedido_id INTEGER, cantidad_solicitada REAL, cantidad_recibida REAL)')
        conn.execute('CREATE TABLE stock_prueba (cantidad REAL)')
        conn.execute('INSERT INTO lineas_pedido VALUES (1,1,10,0)')
        conn.commit()
        barrera = threading.Barrier(2)
        resultados = []
        def recepcion_simultanea():
            db = conectar()
            try:
                db.execute('SELECT cantidad_recibida FROM lineas_pedido WHERE id=1').fetchone()
                barrera.wait(timeout=5)
                compras._registrar_cantidad_pendiente(db.cursor(), 1, 1, 10)
                db.execute('INSERT INTO stock_prueba VALUES (10)')
                db.commit()
                resultados.append('aceptada')
            except (ValueError, sqlite3.OperationalError):
                db.rollback()
                resultados.append('rechazada')
            finally:
                db.close()
        hilos = [threading.Thread(target=recepcion_simultanea) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=10)
        assert not any(hilo.is_alive() for hilo in hilos)
        assert sorted(resultados) == ['aceptada', 'rechazada'], resultados
        assert conn.execute('SELECT SUM(cantidad) FROM stock_prueba').fetchone()[0] == 10
        conn.execute('DROP TABLE lineas_pedido')
        conn.execute('DROP TABLE stock_prueba')
        conn.execute('CREATE TABLE departamentos (id INTEGER, nombre TEXT, empresa_id INTEGER, centro_id INTEGER, activo INTEGER)')
        conn.execute('CREATE TABLE subdepartamentos (id INTEGER, departamento_id INTEGER, empresa_id INTEGER, centro_id INTEGER, activo INTEGER)')
        conn.execute('CREATE TABLE puntos_venta (id INTEGER, departamento_id INTEGER, subdepartamento_id INTEGER, empresa_id INTEGER, centro_id INTEGER, activo INTEGER)')
        conn.execute('CREATE TABLE proveedores (id INTEGER, nombre TEXT, email TEXT, email_pedidos TEXT, telefono TEXT, telefono_pedidos TEXT)')
        conn.execute('''CREATE TABLE pedidos (id INTEGER, proveedor_id INTEGER, empresa_id INTEGER, centro_id INTEGER, departamento_id INTEGER, departamento_solicitante_id INTEGER, subdepartamento_solicitante_id INTEGER, punto_venta_id INTEGER, estado TEXT DEFAULT 'borrador', solicitado_por TEXT, fecha_solicitud_autorizacion TEXT, motivo_rechazo TEXT, rechazado_por TEXT, fecha_rechazo TEXT, aprobado_por TEXT, fecha_aprobacion TEXT, fecha_envio TEXT)''')
        conn.execute("INSERT INTO departamentos VALUES (10, 'Cocina', 1, 1, 1)")
        conn.execute("INSERT INTO departamentos VALUES (11, 'Otro centro', 1, 2, 1)")
        conn.execute("INSERT INTO departamentos VALUES (12, 'Otra empresa', 2, 1, 1)")
        conn.executemany('INSERT INTO subdepartamentos VALUES (?,10,1,1,1)', [(100,), (101,)])
        conn.execute("INSERT INTO proveedores (id,nombre) VALUES (1,'Proveedor prueba')")
        conn.executemany('INSERT INTO pedidos (id,proveedor_id,empresa_id,centro_id,departamento_id,departamento_solicitante_id,subdepartamento_solicitante_id,punto_venta_id) VALUES (?,1,?,?,10,10,?,NULL)', [(1,1,1,100),(2,1,1,101),(3,2,1,100),(4,1,2,100)])
        conn.commit()
        conn.close()
        app = Flask(__name__)
        app.secret_key = 'solo-prueba'
        with app.test_request_context('/'):
            session.update(user_id=1, user_rol='cocinero', user_empresa_id=1, centro_activo_id=1, user_departamento_id=10, user_subdepartamento_id=100)
            with patch.object(compras, 'get_db', side_effect=conectar), patch.object(compras, '_asegurar_estructura_compras'), patch.object(compras, 'url_for', return_value='/compras/pedidos'):
                filtro, params = compras._scope_departamento('p', 1)
                conn = conectar()
                ids = [row['id'] for row in conn.execute('SELECT id FROM pedidos p WHERE ' + filtro, params)]
                conn.close()
                assert ids == [1], ids
                for pedido_id in (2, 3, 4):
                    respuesta = compras.ver_pedido(pedido_id)
                    assert respuesta.status_code == 302, (pedido_id, respuesta.status_code)
                with patch.object(compras, '_pedido_preparado_para_autorizacion', return_value=(True, '')), \
                     patch.object(compras, '_registrar_auditoria_compra'), \
                     patch.object(compras, '_requiere_gestion_compras', return_value=None), \
                     patch.object(compras, 'usuario_tiene_permiso', return_value=True):
                    for pedido_id in (2, 3, 4):
                        compras.solicitar_autorizacion_pedido(pedido_id)
                        compras.autorizar_pedido(pedido_id)
                        compras.rechazar_pedido(pedido_id)
                    conn = conectar()
                    assert [row['estado'] for row in conn.execute('SELECT estado FROM pedidos WHERE id IN (2,3,4) ORDER BY id')] == ['borrador'] * 3
                    conn.close()
                    compras.solicitar_autorizacion_pedido(1)
                    conn = conectar()
                    assert conn.execute('SELECT estado FROM pedidos WHERE id=1').fetchone()['estado'] == 'pendiente_autorizacion'
                    conn.close()
                    compras.autorizar_pedido(1)
                    conn = conectar()
                    assert conn.execute('SELECT estado FROM pedidos WHERE id=1').fetchone()['estado'] == 'autorizado'
                    conn.execute("UPDATE pedidos SET estado='pendiente_autorizacion' WHERE id=1")
                    conn.commit()
                    conn.close()
                    def cambio_concurrente(*_args):
                        otra = conectar()
                        otra.execute("UPDATE pedidos SET estado='rechazado' WHERE id=1")
                        otra.commit()
                        otra.close()
                        return True, ''
                    with patch.object(compras, '_pedido_preparado_para_autorizacion', side_effect=cambio_concurrente):
                        compras.autorizar_pedido(1)
                    conn = conectar()
                    assert conn.execute('SELECT estado FROM pedidos WHERE id=1').fetchone()['estado'] == 'rechazado'
                    conn.execute("UPDATE pedidos SET estado='autorizado' WHERE id=1")
                    conn.commit()
                    conn.close()
                    compras.enviar_pedido(1)
                    conn = conectar()
                    assert conn.execute('SELECT estado FROM pedidos WHERE id=1').fetchone()['estado'] == 'enviado'
                    conn.close()
                    compras.enviar_pedido(1)
                    conn = conectar()
                    assert conn.execute('SELECT estado FROM pedidos WHERE id=1').fetchone()['estado'] == 'enviado'
                    conn.execute("UPDATE pedidos SET estado='autorizado' WHERE id=1")
                    conn.commit()
                    conn.close()
                    with patch.object(compras, '_pedido_preparado_para_autorizacion', side_effect=cambio_concurrente):
                        compras.enviar_pedido(1)
                    conn = conectar()
                    assert conn.execute('SELECT estado FROM pedidos WHERE id=1').fetchone()['estado'] == 'rechazado'
                    conn.close()
                session['user_rol'] = 'admin'
                assert compras._departamento_form_permitido(10)
                assert compras._departamento_recepcion_form_permitido(10)
                assert not compras._departamento_form_permitido(11)
                assert not compras._departamento_recepcion_form_permitido(11)
                assert not compras._departamento_form_permitido(12)
                assert not compras._departamento_recepcion_form_permitido(12)
                session['centro_activo_id'] = None
                assert not compras._departamento_form_permitido(10)
        print('OK: pedidos aislados y recalculo ambiguo de recepcion bloqueado')


if __name__ == '__main__':
    main()

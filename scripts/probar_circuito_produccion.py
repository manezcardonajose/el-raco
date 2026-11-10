"""Prueba aislada del cierre real de produccion sobre SQLite temporal."""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    with tempfile.TemporaryDirectory(prefix='raco_produccion_') as folder:
        path = str(Path(folder) / 'prueba.db')
        os.environ['ERP_DATABASE'] = path
        import database
        from flask import Flask, session
        from blueprints.produccion import routes as pr

        database.init_db()
        def connect():
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            return conn

        app = Flask(__name__)
        app.secret_key = 'prueba-local-aislada'
        with app.test_request_context('/'):
            session.update(user_empresa_id=1, centro_activo_id=1, user_nombre='Prueba')
            conn = connect()
            pr._asegurar_planificacion_schema(conn)
            conn.execute("INSERT INTO productos(id,nombre,precio,unidad,empresa_id) VALUES(1,'Arroz prueba',9,'kg',1)")
            conn.execute("INSERT INTO recetas(id,nombre,empresa_id) VALUES(1,'Arroz prueba',1)")
            conn.execute("INSERT INTO recetas_ingredientes(receta_id,producto_id,cantidad) VALUES(1,1,0.1)")
            conn.execute("INSERT INTO partes_produccion(id,fecha,estado,empresa_id) VALUES(1,'2026-10-23','planificado',1)")
            conn.execute("INSERT INTO lineas_produccion(id,parte_id,receta_id,cantidad_planificada,raciones_planificadas) VALUES(1,1,1,10,10)")
            conn.execute("INSERT INTO lotes(id,codigo_lote,producto_id,cantidad_inicial,cantidad_actual,almacen_id,coste_unitario,empresa_id) VALUES(1,'TEST',1,10,10,1,2,1)")
            conn.execute("INSERT INTO stock_almacen(producto_id,almacen_id,cantidad) VALUES(1,1,15)")
            conn.commit()
            conn.close()
            # Se ejercita el cuerpo real del cierre; autorizacion y APPCC se aislan.
            cierre = pr.completar_parte
            while hasattr(cierre, '__wrapped__'):
                cierre = cierre.__wrapped__
            with patch.object(pr,'get_db',connect), patch.object(pr,'_requiere_gestion_produccion',return_value=None), patch.object(pr,'_requiere_centro_produccion',return_value=None), patch.object(pr,'_scope_partes_produccion',return_value=('empresa_id = ?', [1])), patch.object(pr,'_filtro_lotes_centro',return_value=('', '', [])), patch.object(pr,'_filtro_producto_visible_centro',return_value=('', [])), patch.object(pr,'registrar_appcc_control'), patch.object(pr,'url_for',return_value='/prueba'):
                cierre(1, linea_id=1)
                parcial=connect()
                assert parcial.execute('SELECT estado FROM partes_produccion WHERE id=1').fetchone()[0]=='en_curso'
                assert parcial.execute('SELECT cantidad_actual FROM lotes WHERE id=1').fetchone()[0]==9
                parcial.execute('INSERT INTO lineas_produccion(id,parte_id,receta_id,cantidad_planificada,raciones_planificadas) VALUES(2,1,1,200,200)')
                parcial.commit()
                parcial.close()
                cierre(1, linea_id=2)
                parcial=connect()
                assert parcial.execute('SELECT cantidad_actual FROM lotes WHERE id=1').fetchone()[0]==9
                assert parcial.execute('SELECT COUNT(*) FROM consumo_produccion').fetchone()[0]==1
                assert parcial.execute('SELECT estado FROM partes_produccion WHERE id=1').fetchone()[0]=='en_curso'
                parcial.execute('UPDATE lineas_produccion SET cantidad_real=0 WHERE id=2')
                parcial.commit()
                parcial.close()
                cierre(1)
                conn=connect()
                assert conn.execute('SELECT cantidad_actual FROM lotes WHERE id=1').fetchone()[0] == 9
                assert conn.execute('SELECT cantidad FROM stock_almacen').fetchone()[0] == 14
                assert conn.execute('SELECT coste_real FROM lineas_produccion WHERE id=1').fetchone()[0] == 2
                assert conn.execute('SELECT estado FROM partes_produccion WHERE id=1').fetchone()[0] == 'completado'
                conn.close()
                cierre(1)
                conn=connect()
                assert conn.execute('SELECT COUNT(*) FROM consumo_produccion').fetchone()[0] == 1
                assert conn.execute('SELECT cantidad_actual FROM lotes WHERE id=1').fetchone()[0] == 9
                conn.execute("INSERT INTO recetas(id,nombre,empresa_id) VALUES(2,'Ficha vacia',1)")
                conn.execute("INSERT INTO partes_produccion(id,fecha,estado,empresa_id) VALUES(2,'2026-10-23','planificado',1)")
                conn.execute('INSERT INTO lineas_produccion(id,parte_id,receta_id,cantidad_planificada) VALUES(3,2,2,10)')
                conn.commit()
                conn.close()
                cierre(2)
                conn=connect()
                assert conn.execute('SELECT estado FROM partes_produccion WHERE id=2').fetchone()[0]=='planificado'
                assert conn.execute('SELECT COUNT(*) FROM consumo_produccion WHERE linea_produccion_id=3').fetchone()[0]==0
                conn.close()
            print('OK: cierre, descuento de 1 kg, coste real 2 EUR y segundo cierre sin duplicados')
            from blueprints.compras import routes as compras
            conn=connect()
            linea=dict(id=99, producto_id=1, cantidad_solicitada=5, cantidad_recibida=0, precio_unitario=3, nombre='Arroz prueba')
            pedido=dict(id=99, numero_pedido='PRUEBA', proveedor_id=1)
            form={'cantidad_99':'5', 'almacen_99':'1', 'incidencia_99':'1'}
            with patch.object(compras,'_crear_incidencia_recepcion',return_value=99):
                compras._procesar_linea_recepcion(conn.cursor(),1,pedido,99,linea,form,'Prueba','TEST','2026-10-23','')
            assert conn.execute('SELECT COUNT(*) FROM lotes').fetchone()[0] == 1
            assert conn.execute('SELECT cantidad FROM stock_almacen').fetchone()[0] == 14
            assert conn.execute('SELECT aceptado FROM lineas_recepcion ORDER BY id DESC').fetchone()[0] == 0
            form.pop('incidencia_99')
            with patch.object(compras,'_generar_lote_interno',return_value='RECEPCION-TEST'):
                compras._procesar_linea_recepcion(conn.cursor(),1,pedido,99,linea,form,'Prueba','TEST','2026-10-23','')
            assert conn.execute('SELECT cantidad FROM stock_almacen').fetchone()[0] == 19
            recibido=conn.execute('SELECT coste_unitario,cantidad_actual,id FROM lotes ORDER BY id DESC').fetchone()
            assert recibido['coste_unitario'] == 3 and recibido['cantidad_actual'] == 5
            assert conn.execute('SELECT lote_id FROM lineas_recepcion ORDER BY id DESC').fetchone()[0] == recibido['id']
            conn.close()
            print('OK: rechazo sin entrada; recepcion aceptada +5 kg a 3 EUR/kg con lote enlazado')


if __name__ == '__main__':
    main()

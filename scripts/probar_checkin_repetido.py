"""Verifica que repetir entrada no duplique cobros ni reinicie fianzas."""
import sys
import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch
from flask import Flask
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.recepcion import routes as pms


def main():
    app=Flask(__name__); app.secret_key='prueba'
    entrada=pms.checkin_reserva
    while hasattr(entrada,'__wrapped__'):
        entrada=entrada.__wrapped__
    for estado in ('alojado','checkin','checkout','cancelada','no_show'):
        conn=Mock()
        with app.test_request_context('/',method='POST',data={'importe_pago':'100','emitir_factura':'1'}), patch.object(pms,'get_db',return_value=conn), patch.object(pms,'_ensure_recepcion_schema'), patch.object(pms,'_empresa_id',return_value=1), patch.object(pms,'_reserva_por_id',return_value={'id':1,'estado':estado}), patch.object(pms,'_marcar_habitacion_estado') as marcar, patch.object(pms,'_crear_factura_reserva') as crear, patch.object(pms,'url_for',return_value='/reserva'):
            assert entrada(1).status_code==302
            conn.execute.assert_not_called()
            conn.commit.assert_not_called()
            marcar.assert_not_called()
            crear.assert_not_called()
            conn.close.assert_called_once()
    print('OK: entrada repetida o finalizada no registra otro pago, factura ni cambio de habitacion')
    class ConexionPrueba(sqlite3.Connection):
        def close(self):
            pass

    for deposito in (0,50):
        conn=sqlite3.connect(':memory:',factory=ConexionPrueba)
        conn.row_factory=sqlite3.Row
        conn.executescript('''
            CREATE TABLE reservas_recepcion(
                id,empresa_id,habitacion_id,estado,precio_total,extras_total,
                deposito,saldo,politica_credito,limite_credito,fianza_virtual,
                fianza_referencia,fianza_estado,fianza_liberada_at,fianza_cobrada_at,
                fianza_liquidada_importe,checkin_at,updated_at);
            CREATE TABLE habitaciones_recepcion(id,empresa_id,estado);
            CREATE TABLE pagos_reserva_recepcion(
                id INTEGER PRIMARY KEY,reserva_id,fecha,concepto,importe,forma_pago,
                referencia,observaciones,empresa_id,usuario_id,estado DEFAULT 'confirmado');
            INSERT INTO habitaciones_recepcion VALUES(1,1,'disponible'),(2,2,'disponible');
            INSERT INTO reservas_recepcion VALUES(
                1,1,1,'confirmada',300,0,0,300,'abierto',0,0,'',
                'sin_fianza',NULL,NULL,0,NULL,NULL);
        ''')
        conn.execute('UPDATE reservas_recepcion SET deposito=?,saldo=?',(deposito,300-deposito))
        def buscar(db,id,empresa):
            return db.execute('SELECT * FROM reservas_recepcion WHERE id=? AND empresa_id=?',(id,empresa)).fetchone()
        form={'importe_pago':'100','forma_pago':'efectivo','politica_credito':'fianza_virtual','fianza_virtual':'80'}
        with app.test_request_context('/',method='POST',data=form), patch.object(pms,'get_db',return_value=conn), patch.object(pms,'_ensure_recepcion_schema'), patch.object(pms,'_empresa_id',return_value=1), patch.object(pms,'_reserva_por_id',side_effect=buscar), patch.object(pms,'_actualizar_facturas_reserva'), patch.object(pms,'url_for',return_value='/documentacion'):
            assert entrada(1).status_code==302
            r=buscar(conn,1,1)
            assert r['estado']=='alojado' and r['checkin_at']
            assert r['deposito']==deposito+100 and r['saldo']==200-deposito
            assert r['fianza_virtual']==80 and r['limite_credito']==80
            assert conn.execute('SELECT estado FROM habitaciones_recepcion WHERE id=1').fetchone()[0]=='ocupada'
            assert conn.execute('SELECT estado FROM habitaciones_recepcion WHERE id=2').fetchone()[0]=='disponible'
            movimientos=conn.execute('SELECT COUNT(*) FROM pagos_reserva_recepcion').fetchone()[0]
            assert movimientos==(2 if deposito else 1)
            entrada(1)
            assert conn.execute('SELECT COUNT(*) FROM pagos_reserva_recepcion').fetchone()[0]==movimientos
            assert buscar(conn,1,1)['saldo']==200-deposito
        sqlite3.Connection.close(conn)
    print('OK: entrada valida, pago 100 EUR, deposito previo conservado, saldo correcto, fianza 80 EUR y repeticion sin duplicados')


if __name__=='__main__':
    main()

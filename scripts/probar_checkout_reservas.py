"""Comprueba guardas de salida con el cuerpo real de la ruta PMS."""
import sys
import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch
from flask import Flask

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.recepcion import routes as pms


def main():
    app=Flask(__name__)
    app.secret_key='prueba-aislada'
    salida=pms.checkout_reserva
    while hasattr(salida,'__wrapped__'):
        salida=salida.__wrapped__
    for estado in ('checkout','cancelada','no_show','confirmada','alojado'):
        conn=Mock()
        reserva={'estado':estado,'saldo':100,'habitacion_id':1}
        with app.test_request_context('/',method='POST'), patch.object(pms,'get_db',return_value=conn), patch.object(pms,'_ensure_recepcion_schema'), patch.object(pms,'_empresa_id',return_value=1), patch.object(pms,'_reserva_por_id',return_value=reserva), patch.object(pms,'_recalcular_cargos_reserva') as recalculo, patch.object(pms,'_marcar_habitacion_estado') as habitacion, patch.object(pms,'url_for',return_value='/reserva'):
            response=salida(1)
            assert response.status_code==302
            conn.execute.assert_not_called()
            conn.commit.assert_not_called()
            habitacion.assert_not_called()
            assert recalculo.call_count == (1 if estado=='alojado' else 0)
            conn.close.assert_called_once()
    print('OK: salida repetida, cancelada, no-show, no alojada y saldo pendiente bloqueados sin modificar habitacion')
    class ConexionPrueba(sqlite3.Connection):
        def close(self):
            pass

    for fianza_estado in ('retenida', 'cobrada'):
        conn=sqlite3.connect(':memory:', factory=ConexionPrueba)
        conn.row_factory=sqlite3.Row
        conn.executescript('''
            CREATE TABLE reservas_recepcion(
                id, empresa_id, habitacion_id, estado, precio_total, extras_total,
                deposito, saldo, updated_at, checkout_at, politica_credito,
                limite_credito, fianza_virtual, fianza_referencia, fianza_estado,
                fianza_liberada_at, fianza_cobrada_at, fianza_liquidada_importe);
            CREATE TABLE habitaciones_recepcion(id, empresa_id, estado);
            CREATE TABLE recepcion_cargos_reserva(
                reserva_id, empresa_id, total, estado, incluido, cargo_habitacion);
            CREATE TABLE pagos_reserva_recepcion(reserva_id,empresa_id,importe,estado);
            INSERT INTO habitaciones_recepcion VALUES(1,1,'ocupada'),(2,2,'ocupada');
            INSERT INTO reservas_recepcion VALUES(
                1,1,1,'alojado',300,0,300,0,NULL,NULL,'fianza_virtual',100,100,
                'TEST',NULL,NULL,NULL,0);
            INSERT INTO pagos_reserva_recepcion VALUES(1,1,300,'confirmado');
        ''')
        conn.execute('UPDATE reservas_recepcion SET fianza_estado=?',(fianza_estado,))
        def buscar(db, id, empresa):
            return db.execute('SELECT * FROM reservas_recepcion WHERE id=? AND empresa_id=?',(id,empresa)).fetchone()
        with app.test_request_context('/',method='POST'), patch.object(pms,'get_db',return_value=conn), patch.object(pms,'_ensure_recepcion_schema'), patch.object(pms,'_empresa_id',return_value=1), patch.object(pms,'_reserva_por_id',side_effect=buscar), patch.object(pms,'_actualizar_facturas_reserva'), patch.object(pms,'url_for',return_value='/reserva'):
            salida(1)
            reserva=buscar(conn,1,1)
            assert reserva['estado']=='checkout' and reserva['checkout_at']
            assert reserva['fianza_estado']==('liberada' if fianza_estado=='retenida' else 'cobrada')
            if fianza_estado=='retenida':
                assert reserva['politica_credito']=='sin_credito' and reserva['limite_credito']==0
                assert reserva['fianza_liberada_at']
            assert conn.execute('SELECT estado FROM habitaciones_recepcion WHERE id=1').fetchone()[0]=='limpieza'
            assert conn.execute('SELECT estado FROM habitaciones_recepcion WHERE id=2').fetchone()[0]=='ocupada'
            conn.execute("UPDATE habitaciones_recepcion SET estado='disponible' WHERE id=1")
            salida(1)
            assert conn.execute('SELECT estado FROM habitaciones_recepcion WHERE id=1').fetchone()[0]=='disponible'
        sqlite3.Connection.close(conn)
    print('OK: salida con saldo cero, limpieza, liberacion virtual, fianza cobrada conservada y repeticion inocua')


if __name__=='__main__':
    main()

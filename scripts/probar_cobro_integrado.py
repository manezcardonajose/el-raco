"""Prueba el cuerpo real del cobro con conexiones simultaneas y saldos SQL."""
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from flask import Flask, session
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.recepcion import routes as pms


def main():
    app=Flask(__name__); app.secret_key='prueba'
    cobrar=pms.anadir_pago_reserva
    while hasattr(cobrar,'__wrapped__'):
        cobrar=cobrar.__wrapped__
    with tempfile.TemporaryDirectory() as folder:
        path=str(Path(folder)/'cobros.db')
        def conectar():
            db=sqlite3.connect(path,timeout=15);db.row_factory=sqlite3.Row
            return db
        db=conectar()
        db.executescript('''
            CREATE TABLE reservas_recepcion(id,empresa_id,centro_id,precio_total,extras_total,deposito,saldo,updated_at);
            CREATE TABLE pagos_reserva_recepcion(id INTEGER PRIMARY KEY,reserva_id,fecha,concepto,importe,forma_pago,referencia,estado,observaciones,empresa_id,usuario_id);
            CREATE TABLE recepcion_claves_cobro(empresa_id,reserva_id,clave,PRIMARY KEY(empresa_id,reserva_id,clave));
            CREATE TABLE recepcion_facturas(id,reserva_id,empresa_id,tipo,fecha,total,pagado,saldo,estado,updated_at);
            INSERT INTO reservas_recepcion VALUES(1,1,3,300,0,0,300,NULL),(2,2,8,500,0,0,500,NULL);
            INSERT INTO recepcion_facturas VALUES(1,1,1,'factura','2026-10-25',300,0,300,'emitida',NULL);
        ''');db.close()
        def buscar(db,id,empresa):
            return db.execute('SELECT * FROM reservas_recepcion WHERE id=? AND empresa_id=? AND centro_id=?',(id,empresa,session['centro_activo_id'])).fetchone()
        barrera=Barrier(8)
        def enviar(indice):
            with app.test_request_context('/',method='POST',data={'importe':'100','clave_cobro':'operacion-unica'}):
                session.update(user_empresa_id=1,centro_activo_id=3)
                barrera.wait(timeout=10)
                return cobrar(1).status_code
        with patch.object(pms,'get_db',side_effect=conectar), patch.object(pms,'_ensure_recepcion_schema'), patch.object(pms,'_reserva_por_id',side_effect=buscar), patch.object(pms,'url_for',return_value='/reserva'):
            with ThreadPoolExecutor(max_workers=8) as pool:
                assert list(pool.map(enviar,range(8)))==[302]*8
        db=conectar()
        assert db.execute('SELECT COUNT(*) FROM pagos_reserva_recepcion').fetchone()[0]==1
        assert db.execute('SELECT saldo FROM reservas_recepcion WHERE id=1').fetchone()[0]==200
        assert db.execute('SELECT saldo FROM recepcion_facturas WHERE id=1').fetchone()[0]==200
        assert db.execute('SELECT saldo FROM reservas_recepcion WHERE id=2').fetchone()[0]==500
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        db.close()
    print('OK: ocho cobros concurrentes -> un pago de 100 EUR; reserva y factura con saldo 200 EUR; otra empresa intacta')


if __name__=='__main__':
    main()

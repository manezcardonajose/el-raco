"""Recorrido HTTP real sobre copia temporal, sin sustituir permisos ni rutas."""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
ROOT=Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        path=str(Path(folder)/'http.db')
        source=sqlite3.connect(str(ROOT/'simulacion_el_raco.db')); target=sqlite3.connect(path)
        source.backup(target);source.close();target.close()
        os.environ['ERP_DATABASE']=path
        from app import app
        from flask import session
        from blueprints.recepcion import routes as pms
        app.config['TESTING']=True
        db=sqlite3.connect(path);db.row_factory=sqlite3.Row
        with app.test_request_context('/'):
            session.update(user_empresa_id=1,centro_activo_id=3)
            pms._ensure_recepcion_schema(db)
        for centro in (3,8):
            room=db.execute("INSERT INTO habitaciones_recepcion(numero,nombre,empresa_id,centro_id,capacidad,estado) VALUES(?,?,1,?,2,'disponible')",(f'TEST-{centro}',f'Prueba {centro}',centro)).lastrowid
            reserva=db.execute("INSERT INTO reservas_recepcion(codigo,habitacion_id,empresa_id,centro_id,fecha_entrada,fecha_salida,precio_total,saldo,estado,titular_nombre) VALUES(?,?,1,?,'2026-11-01','2026-11-03',300,300,'confirmada','Prueba HTTP')",(f'HTTP-{centro}',room,centro)).lastrowid
            if centro==3:
                propia=reserva
            else:
                ajena=reserva
        db.commit()
        with app.test_client() as client:
            client.environ_base['HTTP_X_CSRF_TOKEN']='prueba-csrf'
            with client.session_transaction() as sess:
                sess.update(user_id=5,user_nombre='Prueba',user_rol='admin',user_empresa_id=1,user_es_global=1,centro_activo_id=3)
                sess['_csrf_token']='prueba-csrf'
            assert client.get(f'/recepcion/reservas/{propia}').status_code==200
            assert client.post(f'/recepcion/reservas/{propia}/pagos',data={'importe':'100'},headers={'X-CSRF-Token':'incorrecto'}).status_code==400
            assert client.get(f'/recepcion/reservas/{ajena}').status_code==404
            assert client.post(f'/recepcion/reservas/{ajena}/pagos',data={'importe':'100'}).status_code==404
            assert client.post(f'/recepcion/reservas/{propia}/checkin',data={'importe_pago':'100'}).status_code==302
            for _ in range(2):
                assert client.post(f'/recepcion/reservas/{propia}/pagos',data={'importe':'200','clave_cobro':'HTTP-UNICA'}).status_code==302
            r=db.execute('SELECT * FROM reservas_recepcion WHERE id=?',(propia,)).fetchone()
            assert r['estado']=='alojado' and r['saldo']==0 and r['deposito']==300
            assert db.execute('SELECT COUNT(*) FROM pagos_reserva_recepcion WHERE reserva_id=?',(propia,)).fetchone()[0]==2
            assert client.post(f'/recepcion/reservas/{propia}/checkout').status_code==302
            assert db.execute('SELECT estado FROM reservas_recepcion WHERE id=?',(propia,)).fetchone()[0]=='checkout'
            with client.session_transaction() as sess:
                sess.clear()
            assert client.get(f'/recepcion/reservas/{propia}').status_code==302
        db.close()
    print('OK HTTP: detalle, bloqueo entre centros, entrada, dos cobros, reenvio sin duplicado, salida y bloqueo anonimo')


if __name__=='__main__':
    main()

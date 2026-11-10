"""Prueba guardas de centro e idempotencia de anulacion de cobros."""
import sys
from pathlib import Path
from unittest.mock import Mock, patch
from flask import Flask
from werkzeug.exceptions import NotFound
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.recepcion import routes as pms


def main():
    app=Flask(__name__); app.secret_key='prueba'
    anular=pms.anular_pago_reserva
    while hasattr(anular,'__wrapped__'):
        anular=anular.__wrapped__
    for fuera, estado in [(True,'confirmado'),(False,'anulado'),(False,'confirmado')]:
        conn=Mock()
        conn.execute.return_value.fetchone.return_value={'id':1,'estado':estado}
        with app.test_request_context('/',method='POST'), patch.object(pms,'get_db',return_value=conn), patch.object(pms,'_ensure_recepcion_schema'), patch.object(pms,'_empresa_id',return_value=1), patch.object(pms,'_reserva_por_id',return_value=None if fuera else {'id':1}), patch.object(pms,'_recalcular_pagos_reserva') as recalcular, patch.object(pms,'url_for',return_value='/reserva'):
            if fuera:
                try:
                    anular(1,1)
                    raise AssertionError('Reserva fuera de centro aceptada')
                except NotFound:
                    pass
                conn.execute.assert_not_called()
            else:
                assert anular(1,1).status_code==302
            if fuera or estado=='anulado':
                recalcular.assert_not_called()
                conn.commit.assert_not_called()
            else:
                recalcular.assert_called_once_with(conn,1,1)
                conn.commit.assert_called_once()
            conn.close.assert_called_once()
    print('OK: reserva fuera de centro bloqueada; repeticion sin efectos; anulacion valida recalcula saldo')


if __name__=='__main__':
    main()

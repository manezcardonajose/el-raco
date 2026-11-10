"""Regresion de acceso y conservacion de cargos ya facturados."""
import sys
from pathlib import Path
from unittest.mock import Mock, patch
from flask import Flask
from werkzeug.exceptions import NotFound
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.recepcion import routes as pms


def main():
    app=Flask(__name__); app.secret_key='prueba'
    anular=pms.anular_cargo_reserva
    while hasattr(anular,'__wrapped__'):
        anular=anular.__wrapped__
    for fuera, estado, factura in [(True,'pendiente',None),(False,'anulado',None),(False,'pendiente',7),(False,'pendiente',None)]:
        conn=Mock()
        conn.execute.return_value.fetchone.return_value={'id':1,'estado':estado,'factura_id':factura}
        with app.test_request_context('/',method='POST'), patch.object(pms,'get_db',return_value=conn), patch.object(pms,'_ensure_recepcion_schema'), patch.object(pms,'_empresa_id',return_value=1), patch.object(pms,'_reserva_por_id',return_value=None if fuera else {'id':1}), patch.object(pms,'_recalcular_cargos_reserva') as recalcular, patch.object(pms,'url_for',return_value='/detalle'):
            if fuera:
                try:
                    anular(1,1)
                    raise AssertionError('Acceso fuera de centro permitido')
                except NotFound:
                    pass
            else:
                assert anular(1,1).status_code==302
            if fuera or estado=='anulado' or factura:
                recalcular.assert_not_called()
                conn.commit.assert_not_called()
                assert conn.execute.call_count == (0 if fuera else 1)
            else:
                recalcular.assert_called_once_with(conn,1,1)
                conn.commit.assert_called_once()
            conn.close.assert_called_once()
    print('OK: cargo facturado conservado, repeticion inocua, centro protegido y anulacion pendiente permitida')


if __name__=='__main__':
    main()

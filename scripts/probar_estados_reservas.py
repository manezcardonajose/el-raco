"""Comprueba que los cambios genericos no eludan entrada y salida."""
import sys
from pathlib import Path
from unittest.mock import Mock, patch
from flask import Flask
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.recepcion import routes as pms


def main():
    app=Flask(__name__); app.secret_key='prueba'
    casos=[('cambiar_estado_reserva','confirmada','checkout'),
           ('cambiar_estado_reserva','confirmada','alojado'),
           ('cambiar_estado_reserva','alojado','cancelada'),
           ('cambiar_estado_reserva','checkout','confirmada'),
           ('cambiar_estado_reserva','confirmada','invalido'),
           ('no_show_reserva','alojado','no_show'),
           ('no_show_reserva','checkout','no_show')]
    for nombre,actual,destino in casos:
        funcion=getattr(pms,nombre)
        while hasattr(funcion,'__wrapped__'):
            funcion=funcion.__wrapped__
        conn=Mock()
        with app.test_request_context('/',method='POST',data={'estado':destino}), patch.object(pms,'get_db',return_value=conn), patch.object(pms,'_ensure_recepcion_schema'), patch.object(pms,'_empresa_id',return_value=1), patch.object(pms,'_reserva_por_id',return_value={'estado':actual}), patch.object(pms,'url_for',return_value='/reserva'):
            assert funcion(1).status_code==302
            conn.execute.assert_not_called()
            conn.commit.assert_not_called()
            conn.close.assert_called_once()
    print('OK: siete transiciones indebidas bloqueadas sin modificar datos')


if __name__=='__main__':
    main()

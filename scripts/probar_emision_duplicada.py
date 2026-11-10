"""Regresion de la guarda de emision con tipos de documento no validos."""
import sys
from pathlib import Path
from unittest.mock import Mock, patch
from flask import Flask
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.recepcion import routes as pms


def main():
    app=Flask(__name__); app.secret_key='prueba'
    emitir=pms.generar_factura_reserva
    while hasattr(emitir,'__wrapped__'):
        emitir=emitir.__wrapped__
    for tipo in ('factura','FACTURA',' factura ','desconocido',''):
        conn=Mock()
        conn.execute.return_value.fetchone.return_value={'total':330,'lineas':1}
        with app.test_request_context('/',method='POST',data={'tipo':tipo}), patch.object(pms,'get_db',return_value=conn), patch.object(pms,'_ensure_recepcion_schema'), patch.object(pms,'_empresa_id',return_value=1), patch.object(pms,'_reserva_por_id',return_value={'id':1}), patch.object(pms,'_factura_reserva_emitida',return_value={'id':1,'numero_completo':'TEST-1'}), patch.object(pms,'_cargos_pendientes_facturar',return_value={'total_cargos':0}), patch.object(pms,'_crear_factura_reserva') as crear, patch.object(pms,'url_for',return_value='/factura'):
            response=emitir(1)
            assert response.status_code==302
            crear.assert_not_called()
            conn.commit.assert_not_called()
            conn.close.assert_called_once()
    print('OK: reenvio normal, mayusculas, espacios y tipo invalido no duplican la factura existente')


if __name__=='__main__':
    main()

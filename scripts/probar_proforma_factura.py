"""Regresion: una proforma no debe consumir los cargos facturables."""
import inspect
import re
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch
from flask import Flask

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.recepcion import routes as pms


def main():
    conn=sqlite3.connect(':memory:'); conn.row_factory=sqlite3.Row
    source=inspect.getsource(pms._crear_factura_reserva)
    for table in ('recepcion_facturas','recepcion_factura_lineas'):
        columns=re.search(r'INSERT INTO '+table+r'\s*\((.*?)\)\s*VALUES',source,re.S).group(1)
        conn.execute('CREATE TABLE '+table+'(id INTEGER PRIMARY KEY, '+columns+', updated_at)')
    conn.execute('ALTER TABLE recepcion_facturas ADD COLUMN factura_rectificada_id')
    conn.execute('ALTER TABLE recepcion_facturas ADD COLUMN motivo_rectificacion')
    conn.executescript('''
        CREATE TABLE recepcion_cargos_reserva(id,reserva_id,empresa_id,total,estado,incluido,cargo_habitacion,factura_id,fecha,concepto,cantidad,precio_unitario);
        INSERT INTO recepcion_cargos_reserva VALUES(1,1,1,44,'pendiente',0,1,NULL,'2026-10-24','Cena',1,44);
    ''')
    reserva=dict(id=1,precio_total=330,fecha_entrada='2026-10-22',fecha_salida='2026-10-24',titular_nombre='Prueba',titular_email='')
    app=Flask(__name__); app.secret_key='prueba'
    with app.test_request_context('/'), patch.object(pms,'_config_fiscal',return_value=None), patch.object(pms,'_generar_numero_factura',return_value=(2026,1,'TEST-1')), patch.object(pms,'_autocompletar_precio_reserva',side_effect=lambda c,r,e:r), patch.object(pms,'_cliente_fiscal_reserva',return_value=dict(nombre='Prueba',nif='',direccion='',email='')), patch.object(pms,'_impuesto_por_id',return_value=None), patch.object(pms,'_pagado_reserva',return_value=0), patch.object(pms,'_actualizar_huella_factura'):
        pro=pms._crear_factura_reserva(conn,reserva,1,dict(tipo='proforma',iva_estancia=10))
        assert conn.execute('SELECT factura_id FROM recepcion_cargos_reserva').fetchone()[0] is None
        factura=pms._crear_factura_reserva(conn,reserva,1,dict(tipo='factura',iva_estancia=10))
        assert conn.execute('SELECT factura_id FROM recepcion_cargos_reserva').fetchone()[0]==factura
        for id in (pro,factura):
            f=conn.execute('SELECT * FROM recepcion_facturas WHERE id=?',(id,)).fetchone()
            assert f['total']==374 and f['base']==340 and f['impuestos']==34
        assert pms._cargos_pendientes_facturar(conn,1,1)['total_cargos']==0
        original=conn.execute('SELECT * FROM recepcion_facturas WHERE id=?',(factura,)).fetchone()
        lineas=conn.execute('SELECT * FROM recepcion_factura_lineas WHERE factura_id=?',(factura,)).fetchall()
        assert pms._crear_factura_rectificativa(conn,original,lineas,2,{}) is None
        rect=pms._crear_factura_rectificativa(conn,original,lineas,1,{})
        abono=conn.execute('SELECT * FROM recepcion_facturas WHERE id=?',(rect,)).fetchone()
        assert abono['base']==-340 and abono['impuestos']==-34 and abono['total']==-374
        assert abono['factura_rectificada_id']==factura
        assert conn.execute('SELECT estado FROM recepcion_facturas WHERE id=?',(factura,)).fetchone()[0]=='rectificada'
        assert pms._crear_factura_rectificativa(conn,original,lineas,1,{}) is None
        assert conn.execute("SELECT COUNT(*) FROM recepcion_facturas WHERE tipo='rectificativa'").fetchone()[0]==1
    conn.close()
    print('OK: proforma conserva cargos; factura incluye estancia y cena; base 340 + impuesto 34 = 374 EUR')
    print('OK: rectificativa -374 EUR; duplicado y empresa incorrecta bloqueados')


if __name__=='__main__':
    main()

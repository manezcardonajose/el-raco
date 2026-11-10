"""Verifica que un cobro no se duplique entre facturas de una reserva."""
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.recepcion import routes as pms


def main():
    conn=sqlite3.connect(':memory:'); conn.row_factory=sqlite3.Row
    conn.executescript('''
        CREATE TABLE recepcion_facturas(id,reserva_id,empresa_id,tipo,fecha,total,pagado,saldo,estado,updated_at);
        INSERT INTO recepcion_facturas VALUES
            (1,1,1,'factura','2026-10-01',300,0,300,'emitida',NULL),
            (2,1,1,'factura','2026-10-02',40,0,40,'emitida',NULL),
            (3,1,1,'proforma','2026-09-30',340,0,340,'emitida',NULL),
            (4,1,2,'factura','2026-10-01',500,0,500,'emitida',NULL);
    ''')
    for cobrado, esperados in [(100,(100,0)),(320,(300,20)),(340,(300,40)),(0,(0,0))]:
        with patch.object(pms,'_pagado_reserva',return_value=cobrado):
            pms._actualizar_facturas_reserva(conn,1,1)
        filas=conn.execute("SELECT * FROM recepcion_facturas WHERE empresa_id=1 AND tipo='factura' ORDER BY id").fetchall()
        assert tuple(f['pagado'] for f in filas)==esperados
        assert sum(f['pagado'] for f in filas)<=cobrado
        assert all(f['saldo']==f['total']-f['pagado'] for f in filas)
        assert conn.execute('SELECT pagado FROM recepcion_facturas WHERE id=4').fetchone()[0]==0
    conn.close()
    print('OK: cobros parciales, liquidacion, anulacion, proforma y aislamiento de empresa')


if __name__=='__main__':
    main()

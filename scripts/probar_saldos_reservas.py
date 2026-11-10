"""Regresion de cargos, cobros y anulaciones con funciones reales del PMS."""
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blueprints.recepcion import routes as pms


def main():
    conn=sqlite3.connect(':memory:')
    conn.row_factory=sqlite3.Row
    conn.executescript('''
        CREATE TABLE reservas_recepcion(id,empresa_id,precio_total,extras_total,deposito,saldo,updated_at);
        CREATE TABLE pagos_reserva_recepcion(id,reserva_id,empresa_id,importe,estado);
        CREATE TABLE recepcion_cargos_reserva(id,reserva_id,empresa_id,total,estado,incluido,cargo_habitacion);
        INSERT INTO reservas_recepcion VALUES(1,1,300,0,0,300,NULL),(2,2,500,0,0,500,NULL);
        INSERT INTO recepcion_cargos_reserva VALUES
            (1,1,1,40,'pendiente',0,1),
            (2,1,1,90,'anulado',0,1),
            (3,1,1,50,'pendiente',1,1),
            (4,1,1,60,'pendiente',0,0),
            (5,1,2,1000,'pendiente',0,1);
    ''')
    # La emision/actualizacion de facturas se probara en su circuito propio.
    with patch.object(pms,'_actualizar_facturas_reserva'):
        pms._recalcular_cargos_reserva(conn,1,1)
        row=conn.execute('SELECT * FROM reservas_recepcion WHERE id=1').fetchone()
        assert row['extras_total']==40 and row['saldo']==340
        conn.execute("INSERT INTO pagos_reserva_recepcion VALUES(1,1,1,100,'confirmado')")
        pms._recalcular_pagos_reserva(conn,1,1)
        assert conn.execute('SELECT saldo FROM reservas_recepcion WHERE id=1').fetchone()[0]==240
        conn.execute("INSERT INTO pagos_reserva_recepcion VALUES(2,1,1,240,'confirmado')")
        pms._recalcular_pagos_reserva(conn,1,1)
        assert conn.execute('SELECT saldo FROM reservas_recepcion WHERE id=1').fetchone()[0]==0
        conn.execute("UPDATE pagos_reserva_recepcion SET estado='anulado' WHERE id=2")
        pms._recalcular_pagos_reserva(conn,1,1)
        assert conn.execute('SELECT saldo FROM reservas_recepcion WHERE id=1').fetchone()[0]==240
        pms._recalcular_cargos_reserva(conn,1,2)
        assert conn.execute('SELECT saldo FROM reservas_recepcion WHERE id=2').fetchone()[0]==500
    conn.close()
    print('OK: estancia 300 + extras 40; cobro 100 -> saldo 240; liquidacion -> 0; anulacion -> 240; empresa ajena intacta')


if __name__=='__main__':
    main()

"""Regresion de solapamientos y bloqueos usando consultas reales del PMS."""
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.recepcion import routes as pms


def main():
    conn=sqlite3.connect(':memory:'); conn.row_factory=sqlite3.Row
    conn.executescript('''
        CREATE TABLE reservas_recepcion(id,codigo,titular_nombre,habitacion_id,empresa_id,fecha_entrada,fecha_salida,estado);
        CREATE TABLE bloqueos_habitacion_recepcion(id,motivo,descripcion,habitacion_id,empresa_id,fecha_inicio,fecha_fin,estado);
        INSERT INTO reservas_recepcion VALUES(1,'TEST','Prueba',1,1,'2026-10-10','2026-10-15','confirmada');
        INSERT INTO reservas_recepcion VALUES(2,'OTRA','Prueba',1,2,'2026-10-15','2026-10-20','confirmada');
        INSERT INTO bloqueos_habitacion_recepcion VALUES(1,'averia','Prueba',1,1,'2026-10-20','2026-10-22','activo');
    ''')
    assert not pms._habitacion_disponible(conn,1,'2026-10-12','2026-10-14',1)
    assert not pms._habitacion_disponible(conn,1,'2026-10-09','2026-10-16',1)
    assert pms._habitacion_disponible(conn,1,'2026-10-15','2026-10-20',1)
    assert pms._habitacion_disponible(conn,1,'2026-10-08','2026-10-10',1)
    assert pms._habitacion_disponible(conn,1,'2026-10-10','2026-10-15',1,1)
    assert not pms._habitacion_disponible(conn,1,'2026-10-20','2026-10-21',1)
    conn.execute("UPDATE reservas_recepcion SET estado='cancelada' WHERE id=1")
    assert pms._habitacion_disponible(conn,1,'2026-10-10','2026-10-15',1)
    conn.close()
    print('OK: solapamiento parcial/total, fechas contiguas, edicion propia, cancelacion, bloqueo y aislamiento de empresa')


if __name__=='__main__':
    main()

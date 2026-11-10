"""Verifica exclusion entre dos escrituras de disponibilidad en SQLite."""
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.recepcion import routes as pms


def main():
    with tempfile.TemporaryDirectory() as folder:
        path=str(Path(folder)/'prueba.db')
        conn=sqlite3.connect(path)
        conn.executescript('''
            CREATE TABLE reservas_recepcion(id INTEGER PRIMARY KEY,codigo,titular_nombre,habitacion_id,empresa_id,fecha_entrada,fecha_salida,estado);
            CREATE TABLE bloqueos_habitacion_recepcion(id,motivo,descripcion,habitacion_id,empresa_id,fecha_inicio,fecha_fin,estado);
        ''')
        conn.close()
        barrera=Barrier(2)
        def reservar(numero):
            db=sqlite3.connect(path,timeout=5); db.row_factory=sqlite3.Row
            try:
                barrera.wait(timeout=5)
                db.execute('BEGIN IMMEDIATE')
                if not pms._habitacion_disponible(db,1,'2026-11-01','2026-11-03',1):
                    db.rollback()
                    return False
                db.execute("INSERT INTO reservas_recepcion VALUES(?,?,'Prueba',1,1,'2026-11-01','2026-11-03','confirmada')",(numero,str(numero)))
                db.commit()
                return True
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados=list(pool.map(reservar,(1,2)))
        assert sum(resultados)==1
        print('OK: dos intentos simultaneos, una reserva aceptada y otra bloqueada por disponibilidad')


if __name__=='__main__':
    main()

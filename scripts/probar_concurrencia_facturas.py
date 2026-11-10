"""Comprueba numeracion con ocho conexiones SQLite simultaneas."""
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
        path=str(Path(folder)/'facturas.db')
        conn=sqlite3.connect(path)
        conn.execute('CREATE TABLE recepcion_facturas(empresa_id,tipo,serie,ejercicio,numero,numero_completo)')
        conn.close()
        barrera=Barrier(8)
        def emitir(indice):
            db=sqlite3.connect(path,timeout=10); db.row_factory=sqlite3.Row
            try:
                barrera.wait(timeout=10)
                ejercicio,numero,codigo=pms._generar_numero_factura(db,1,'factura','TEST','2026-10-25')
                db.execute('INSERT INTO recepcion_facturas VALUES(?,?,?,?,?,?)',(1,'factura','TEST',ejercicio,numero,codigo))
                db.commit()
                return numero
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            numeros=list(pool.map(emitir,range(8)))
        assert sorted(numeros)==list(range(1,9)), numeros
        conn=sqlite3.connect(path); conn.row_factory=sqlite3.Row
        for empresa,tipo,serie,fecha in [(2,'factura','TEST','2026-10-25'),(1,'factura','OTRA','2026-10-25'),(1,'factura','TEST','2027-01-01')]:
            assert pms._generar_numero_factura(conn,empresa,tipo,serie,fecha)[1]==1
            conn.rollback()
        conn.close()
    print('OK: ocho emisiones simultaneas con numeros 1..8 unicos; empresa, serie y ejercicio independientes')


if __name__=='__main__':
    main()

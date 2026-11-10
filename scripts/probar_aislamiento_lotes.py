"""Verifica aislamiento real de existencias entre centros y empresas."""
import sqlite3
import sys
from pathlib import Path
from flask import Flask, session
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.produccion import routes as pr


def main():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''
        CREATE TABLE almacenes(id,centro_id,empresa_id);
        CREATE TABLE lotes(id,producto_id,empresa_id,almacen_id,cantidad_actual);
        INSERT INTO almacenes VALUES(1,1,1),(2,3,1),(3,1,2);
        INSERT INTO lotes VALUES(1,1,1,1,10),(2,1,1,2,20),(3,1,1,NULL,30),(4,1,1,3,40);
    ''')
    app=Flask(__name__);app.secret_key='prueba'
    with app.test_request_context('/'):
        for centro,esperado in [(1,1),(3,2)]:
            session['centro_activo_id']=centro
            join,filtro,params=pr._filtro_lotes_centro(db)
            rows=db.execute('SELECT l.id FROM lotes l '+join+' WHERE l.empresa_id=1 '+filtro,params).fetchall()
            assert [r['id'] for r in rows]==[esperado]
        db.execute('ALTER TABLE almacenes RENAME COLUMN centro_id TO centro_antiguo')
        join,filtro,params=pr._filtro_lotes_centro(db)
        assert not db.execute('SELECT l.id FROM lotes l '+join+' WHERE l.empresa_id=1 '+filtro,params).fetchall()
    db.close()
    print('OK: Hotel Pepe y Hotel 1 separados; lotes sin almacen y almacenes de otra empresa excluidos; esquema incompleto no amplia acceso')


if __name__=='__main__':
    main()

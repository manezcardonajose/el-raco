"""Comprueba caducidad, descongelacion y empresa antes de consumir."""
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch
from flask import Flask
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.produccion import routes as pr


def main():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''
        CREATE TABLE lotes(id,producto_id,empresa_id,estado,cantidad_actual,estado_descongelado,fecha_caducidad,fecha_ultimo_control);
        INSERT INTO lotes VALUES
          (1,1,1,'activo',5,'apto','2000-01-01',NULL),
          (2,1,1,'activo',5,'en_curso','2099-01-01',NULL),
          (3,1,1,'activo',5,'apto','2099-01-01',NULL),
          (4,1,2,'activo',5,'apto','2099-01-01',NULL),
          (5,1,1,'activo',5,'no_apto','2099-01-01',NULL);
    ''')
    app=Flask(__name__);app.secret_key='prueba'
    with app.test_request_context('/'), patch.object(pr,'_filtro_lotes_centro',return_value=('', '', [])):
        for congelado in (True,False):
            with patch.object(pr,'_producto_requiere_alerta_descongelado',return_value=congelado):
                lotes,_=pr._lotes_disponibles_para_consumo(db,{'producto_id':1},1)
                assert [l['id'] for l in lotes]==[3]
    db.close()
    print('OK: caducados, descongelacion en curso, no aptos y otra empresa excluidos')


if __name__=='__main__':
    main()
